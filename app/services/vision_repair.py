"""One bounded proposal per flagged region, accepted only after local validation."""
import math
from collections import Counter
from pathlib import Path
from types import SimpleNamespace as N
import pymupdf as fitz
from app.config import StudioError
from app.agents.checker import CheckerAgent
from app.services.renderer import Renderer
from app.services.layout_tools import fits, minimum_body_size
from app.services.typography import diagnostics, neighbour_edges, penalty

REPAIRABLE = {'overflow','collision','clipping','small_text','font_size_inconsistent'}
HARD = {'overflow','collision','clipping','missing_text','protected_value_changed','graphics_changed','page_dimensions_changed'}


def candidate_layout(entry, proposal, entries, blocks, page, graphics, fonts, floor, allow_box=True):
    block,translation,layout = entry
    if proposal.block_id != block.id:
        raise ValueError('Unknown block')
    new=N(bbox=list(layout.bbox),font=layout.font,font_size=layout.font_size,fit_status='fitted',line_breaks=list(layout.line_breaks))
    types=[a.type for a in proposal.actions]
    if not types or any(t in {'no_change','manual_review'} for t in types) or len(set(types))!=len(types):
        raise ValueError('No actionable repair or conflicting actions')
    for action in proposal.actions:
        if action.type=='set_font_size':
            if not isinstance(action.value,(float,int)) or not math.isfinite(action.value):
                raise ValueError('Invalid size')
            new.font_size=action.value
        elif action.type=='set_bbox':
            value=action.value
            if not allow_box or not isinstance(value,list) or len(value)!=4 or not all(math.isfinite(v) for v in value):
                raise ValueError('Invalid box')
            new.bbox=value
        elif action.type=='set_font_width':
            if action.value not in {'normal','semi_condensed','condensed'}:
                raise ValueError('Invalid font width')
            path=fonts.resolve(translation.target_language,translation.script,block.style.get('bold',False),width=action.value)
            if path=='missing':
                raise ValueError('Unavailable font width')
            new.font=path or 'sans-serif'
            runs=translation.metadata_json.get('rich_text',{}).get('runs') if block.style.get('rich_text') else None
            for run in runs or [{'text':translation.text,'style':block.style}]:
                selected=fonts.resolve(translation.target_language,translation.script,run['style'].get('bold',False),width=action.value)
                if selected=='missing':
                    raise ValueError('Unavailable weight')
                if not fonts.supports(run['text'], selected, run['style'].get('bold', False)):
                    raise ValueError('Missing glyph')
    minimum=min(floor,float(block.style['size']))
    if not minimum <= new.font_size <= float(block.style['size']) or minimum_body_size(block,translation,new.font_size)<minimum-.01:
        raise ValueError('Unreadable or oversized text')
    old=fitz.Rect(layout.bbox);box=fitz.Rect(new.bbox)
    if box.is_empty or not fitz.Rect(0,0,page.width,page.height).contains(box):
        raise ValueError('Outside page')
    cell=block.style.get('table_cell_bbox')
    if cell and not fitz.Rect(cell).contains(box):
        raise ValueError('Outside table cell')
    if any(abs(a-b)>24 for a,b in zip(new.bbox,layout.bbox)) or box.get_area()>old.get_area()*1.5:
        raise ValueError('Box change exceeds repair bounds')
    by_id={b.id:l for b,_,l in entries}
    obstacles=[fitz.Rect(by_id[b.id].bbox if b.id in by_id else b.bbox) for b in blocks if b.id!=block.id]
    obstacles += [fitz.Rect(r) for r in graphics]
    if any((box & r).get_area()>max(1.,(old & r).get_area())+.01 for r in obstacles):
        raise ValueError('New overlap')
    if not fits(block,translation,new,fonts,page):
        raise ValueError('Proposed text does not fit')
    return new


def quality(issues, entries, floor):
    shortfall=sum(max(0,min(floor,b.style['size'])-minimum_body_size(b,t,l.font_size))**2
                  for b,t,l in entries if l.fit_status=='fitted')
    return (sum(i['code'] in HARD for i in issues),shortfall+penalty(entries,neighbour_edges(entries)))


def review_page(reviewer, settings, source, destination, page, entries, blocks, graphics, issues, fonts, stats):
    if stats.get('unavailable'):
        return issues
    flags=[(i.get('block_id'),i['code']) for i in issues]
    flags += [(b.id,code) for b,code,_ in diagnostics(entries,settings.studio_typography_min_font_pt)]
    targets=[]
    for identity,code in flags:
        if code in REPAIRABLE and identity and identity not in targets:
            targets.append(identity)
    for identity in targets:
        if stats['calls'] >= settings.studio_vision_max_regions:
            break
        index=next((i for i,(b,_,_) in enumerate(entries) if b.id==identity),None)
        if index is None:
            continue
        block,translation,layout=entries[index]
        if any(block.style.get(k) for k in ('ocr','vertical','superscript','unsupported_ocr')):
            continue
        region=(fitz.Rect(block.bbox)|fitz.Rect(layout.bbox))+(-12,-12,12,12)
        region &= fitz.Rect(0,0,page.width,page.height)
        zoom=min(2.,1024/max(region.width,region.height))
        with fitz.open(source) as original,fitz.open(destination) as rendered:
            src=original[page.number-1].get_pixmap(matrix=fitz.Matrix(zoom,zoom),clip=region).tobytes('png')
            dst=rendered[0].get_pixmap(matrix=fitz.Matrix(zoom,zoom),clip=region).tobytes('png')
        context={'block_id':identity,'issues':[code for bid,code in flags if bid==identity],
                 'bbox':layout.bbox,'font_size_pt':layout.font_size,'source_size_pt':block.style['size'],
                 'minimum_font_size_pt':min(settings.studio_typography_min_font_pt,block.style['size']),
                 'page_size_pt':[page.width,page.height],'cell_bbox':block.style.get('table_cell_bbox'),
                 'crop_bbox_pt':list(region),'maximum_box_coordinate_change_pt':24}
        stats['calls']+=1
        record={'page':page.number,'block_id':identity,'status':'rejected'}
        stats['reviews'].append(record)
        candidate=Path(destination).with_name(Path(destination).stem+'-candidate.pdf')
        try:
            proposal,usage=reviewer.review(context,src,dst)
            for key,value in usage.items():
                stats['usage'][key]=stats['usage'].get(key,0)+value
            record['proposal']=proposal.model_dump()
            if not proposal.actions or all(a.type in {'no_change','manual_review'} for a in proposal.actions):
                record['status']='manual_review' if any(a.type=='manual_review' for a in proposal.actions) else 'no_change'
                continue
            replacement=candidate_layout(entries[index],proposal,entries,blocks,page,graphics,fonts,
                settings.studio_typography_min_font_pt,allow_box=page.metadata_json.get('layout_mode')!='spans')
            proposed=list(entries);proposed[index]=(block,translation,replacement)
            Renderer(fonts).render_page(source,page.number,proposed,candidate)
            checked=CheckerAgent().run(source,candidate,page.number,proposed)
            before_counts=Counter(i['code'] for i in issues)
            after_counts=Counter(i['code'] for i in checked)
            if any(after_counts[code]>before_counts[code] for code in HARD):
                raise ValueError('A rendering check became worse')
            if quality(checked,proposed,settings.studio_typography_min_font_pt)>=quality(issues,entries,settings.studio_typography_min_font_pt):
                raise ValueError('Local checks did not improve')
            candidate.replace(destination)
            candidate.with_suffix('.png').replace(Path(destination).with_suffix('.png'))
            for key in ('bbox','font','font_size','fit_status','line_breaks'):
                setattr(layout,key,getattr(replacement,key))
            issues=checked;stats['accepted']+=1;record['status']='accepted'
        except StudioError as exc:
            record['status']='unavailable';record['reason']=exc.code
            stats['unavailable']=True
            # Authentication/provider outages must not consume the rest of the budget.
            break
        except (ValueError,RuntimeError) as exc:
            record['reason']=str(exc)[:150]
        finally:
            candidate.unlink(missing_ok=True);candidate.with_suffix('.png').unlink(missing_ok=True)
    return issues
