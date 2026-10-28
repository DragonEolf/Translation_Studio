"""Bounded local smoothing of relative font scales, never global minimum sizing."""
import math
from types import SimpleNamespace
import pymupdf as fitz
from app.services.layout_tools import fits, minimum_body_size


def eligible(entry):
    block, _, layout = entry
    return (layout.fit_status == 'fitted' and not any(block.style.get(key) for key in
            ('ocr', 'vertical', 'superscript')) and float(block.style['size']) > 0)


def neighbour_edges(entries):
    """Compare nearby peers within a column/cell; never headings against body."""
    edges = []
    for i, (block, _, layout) in enumerate(entries):
        if not eligible(entries[i]):
            continue
        a = fitz.Rect(layout.bbox)
        nearby = []
        for j in range(i+1, len(entries)):
            other, _, other_layout = entries[j]
            if not eligible(entries[j]) or getattr(block, 'type', None) != getattr(other, 'type', None):
                continue
            if block.style.get('table_cell_bbox') != other.style.get('table_cell_bbox'):
                continue
            b = fitz.Rect(other_layout.bbox)
            if (a & b).get_area() > 1:
                continue
            overlap = min(a.x1,b.x1)-max(a.x0,b.x0)
            gap = max(0, max(a.y0,b.y0)-min(a.y1,b.y1))
            size = max(float(block.style['size']),float(other.style['size']))
            if overlap < .6*min(a.width,b.width) or gap > 3*size:
                continue
            if abs(a.x0-b.x0) > max(12, .25*max(a.width,b.width)):
                continue
            distance = gap/size + abs(a.x0-b.x0)/max(1,size)
            nearby.append((distance,j))
        for distance,j in sorted(nearby)[:3]:
            edges.append((i,j,1/(1+distance)))
    return edges


def scales(entries):
    return [float(l.font_size)/float(b.style['size']) for b,_,l in entries]


def penalty(entries, edges):
    values = scales(entries)
    return sum(w*math.log(max(values[i],1e-6)/max(values[j],1e-6))**2 for i,j,w in edges)


def diagnostics(entries, min_size=6):
    warnings = []
    edges = neighbour_edges(entries)
    values = scales(entries)
    uneven = {index for i,j,_ in edges if abs(math.log(max(values[i],1e-6)/max(values[j],1e-6))) > math.log(1.25) for index in (i,j)}
    for index,(block,translation,layout) in enumerate(entries):
        if not eligible(entries[index]):
            continue
        floor = min(float(block.style['size']),min_size)
        if minimum_body_size(block,translation,layout.font_size) < floor-.05:
            warnings.append((block,'small_text','Translated body text is below the configured readable size; review this region.'))
        if index in uneven:
            warnings.append((block,'font_size_inconsistent','Nearby comparable text has substantially different relative font sizes.'))
    return warnings


class TypographyPass:
    def __init__(self, fonts, min_size=6, max_refits=24):
        self.fonts,self.min_size,self.max_refits = fonts,min_size,max_refits

    def apply(self, entries, page):
        edges = neighbour_edges(entries)
        values = scales(entries)
        proposals = []
        for i,(block,translation,layout) in enumerate(entries):
            if not eligible(entries[i]):
                continue
            peers = [(j,w) if i==a else (a,w) for a,j,w in edges if i in (a,j)]
            floor = min(float(block.style['size']),self.min_size)
            small = minimum_body_size(block,translation,layout.font_size) < floor-.05
            uneven = any(abs(math.log(max(values[i],1e-6)/max(values[j],1e-6))) > math.log(1.25) for j,_ in peers)
            if not small and not uneven:
                continue
            if not small:
                peers=[(j,w) for j,w in peers if minimum_body_size(entries[j][0],entries[j][1],entries[j][2].font_size)
                       >= min(self.min_size,entries[j][0].style['size'])-.05]
            # One coordinate step for the quadratic penalty in log scale.
            total = sum(w for _,w in peers)
            target = math.exp((math.log(max(values[i],1e-6)) + 2*sum(w*math.log(max(values[j],1e-6)) for j,w in peers))/(1+2*total))
            target_size = min(float(block.style['size']),max(floor, target*float(block.style['size'])))
            # Never sacrifice a readable neighbourhood to a single tiny outlier.
            target_size = max(target_size,layout.font_size*.9)
            if small:
                ratio = minimum_body_size(block,translation,1.)
                target_size = min(float(block.style['size']),max(target_size,floor/max(ratio,.01)))
            proposals.append((not small, -abs(target_size-layout.font_size), i,target_size))
        changed=[]; trials=0
        for _,_,index,target in sorted(proposals):
            if trials >= self.max_refits:
                break
            block,translation,layout = entries[index]
            if abs(target-layout.font_size)<.05:
                continue
            old=layout.font_size
            old_penalty=penalty(entries,edges)
            old_small=minimum_body_size(block,translation,old)
            candidate=SimpleNamespace(bbox=layout.bbox,font=layout.font,font_size=target)
            trials+=1
            if not fits(block,translation,candidate,self.fonts,page):
                continue
            layout.font_size=target
            improved = penalty(entries,edges) < old_penalty-1e-6
            readable = old_small < min(self.min_size,block.style['size'])-.05 and minimum_body_size(block,translation,target)>old_small+.05
            if improved or readable:
                changed.append(block.id)
            else:
                layout.font_size=old
        return {'changed_blocks':changed,'refits':trials,'neighbour_penalty':round(penalty(entries,edges),6)}
