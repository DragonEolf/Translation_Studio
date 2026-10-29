"""Deterministic source-layout policy: confident continuation, otherwise anchoring.

Extraction block IDs are provenance, not paragraph boundaries. Cross-block flow
requires independent geometric and textual evidence and never crosses a barrier.
"""
import re
from collections import Counter
from statistics import median
import pymupdf as fitz

VERSION = 1
LIST_START = re.compile(r'^\s*(?:[●•▪*\-–]|\d+[.)]|[A-Za-z][.)])\s+')
ANCHOR_TYPES = {'heading', 'header', 'footer', 'caption', 'footnote', 'table_cell'}


def box_union(boxes):
    result = fitz.Rect(boxes[0])
    for box in boxes[1:]:
        result |= fitz.Rect(box)
    return result


def members(chunk):
    return [b for line in chunk['lines'] for _, b in line['spans']]


def role(chunk, allow_short=False):
    blocks = members(chunk)
    text = ' '.join(line['text'] for line in chunk['lines']).strip()
    if any(b['style'].get('table_cell_bbox') for b in blocks):
        return 'cell'
    if any(b['type'] in ANCHOR_TYPES or b['style'].get('vertical') for b in blocks):
        return 'anchored'
    if LIST_START.match(text):
        return 'list'
    # Contacts, field labels, equations, code, short titles and all-bold entries
    # need their positions more than they need sentence-level reflow.
    weights = Counter()
    for b in blocks:
        weights[bool(b['style'].get('bold'))] += len(b['source_text'])
    if (weights[True] > sum(weights.values())*.8 or text.endswith(':')
            or (not allow_short and (len(text.split()) < 4 or len(re.findall(r'[^\W\d_]',text)) < 25))
            or re.search(r'https?://|\S+@\S+|\t| {3,}',text)
            or re.search(r'[=≠≤≥∑∫{}]|\b(?:def |class |SELECT |INSERT )',text)
            or (text.isupper() and len(text) < 120)):
        return 'anchored'
    return 'prose'


def base_size(chunk):
    weights = Counter()
    for b in members(chunk):
        if not b['style'].get('superscript'):
            weights[round(float(b['style']['size']),1)] += len(b['source_text'])
    return weights.most_common(1)[0][0] if weights else 12.


def visible_barriers(page):
    return [fitz.Rect(r) for r in page.get('layout_barriers', [])]


def continuation(previous, current, page, blocks, column_right):
    """Return an evidence reason for a safe merge, or None for uncertainty."""
    a, b = previous['lines'][-1], current['lines'][0]
    prior_role, next_role = role(previous), role(current, allow_short=True)
    if prior_role not in {'prose','list'} or next_role != 'prose':
        return None
    size = base_size(previous)
    if abs(size-base_size(current)) > max(.5,size*.05):
        return None
    ra, rb = fitz.Rect(a['bbox']), fitz.Rect(b['bbox'])
    gap = rb.y0-ra.y1
    if not -.1*size <= gap <= .8*size or rb.y0 <= ra.y0+size*.6:
        return None
    indent = rb.x0-ra.x0
    allowed_indent = size*1.5 if prior_role == 'list' and len(previous['lines']) == 1 else size*.4
    if abs(indent) > allowed_indent or (prior_role == 'list' and indent < -size*.4):
        return None
    # A short last line, complete standalone sentence, or label is a boundary.
    # Never join two resume entries simply because their left margins match.
    width = max(ra.width, column_right-ra.x0)
    if ra.width < max(size*8, width*.72) or a['text'].rstrip().endswith((':',';','.','!','?','。','।')):
        return None
    identities = {b['id'] for b in members(previous)+members(current)}
    bounds = box_union([line['bbox'] for line in previous['lines']+current['lines']])
    if any((bounds & fitz.Rect(other['bbox'])).get_area() > 1
           for other in blocks if other['id'] not in identities):
        return None
    band = fitz.Rect(bounds.x0,ra.y1,bounds.x1,max(ra.y1+.1,rb.y0))
    if any((band & (r+(-.25,-.25,.25,.25))).get_area() > .1
           for r in visible_barriers(page)
           if not r.contains(bounds) and not ((r & ra).get_area()>1 and (r & rb).get_area()>1)):
        return None
    return 'aligned_continuation_with_matching_style_and_line_spacing'


def plan_chunks(chunks, pages, blocks):
    """Join source chunks only on strong continuation evidence, within a page."""
    planned=[]
    for pn,page in enumerate(pages,1):
        source=[c for c in chunks if c['pn']==pn]
        source.sort(key=lambda c:(c['lines'][0]['bbox'][1],c['lines'][0]['bbox'][0]))
        page_blocks=[b for b in blocks if b['page_number']==pn]
        groups=[]
        for chunk in source:
            line=chunk['lines'][0]; size=base_size(chunk)
            aligned=[fitz.Rect(l['bbox']) for c in source for l in c['lines']
                     if abs(l['bbox'][0]-line['bbox'][0]) <= size*.4
                     and abs(l['bbox'][1]-line['bbox'][1]) < page['height']*.5]
            right=max((r.x1 for r in aligned),default=line['bbox'][2])
            candidates=sorted((c for c in groups
                if -.1*size <= line['bbox'][1]-c['lines'][-1]['bbox'][3] <= .8*size
                and abs(line['bbox'][0]-c['lines'][-1]['bbox'][0])<=size*1.5),
                key=lambda c:c['lines'][-1]['bbox'][1],reverse=True)
            parent=None
            for candidate in candidates:
                reason=continuation(candidate,chunk,page,page_blocks,right)
                if reason:
                    parent=candidate;break
            if parent:
                parent['lines'].extend(chunk['lines'])
                parent['cross_block']=True
            else:
                groups.append({**chunk,'lines':list(chunk['lines']),'cross_block':False})
        planned.extend(groups)
    return planned


def annotate_policy(block, chunk):
    kind=role(chunk)
    multiline=len(chunk['lines'])>1
    decision='reflow' if multiline and kind in {'prose','list'} else 'cell' if kind=='cell' else 'formatted' if len(members(chunk))>1 else 'anchor'
    if chunk.get('cross_block') and decision=='reflow':
        block['style']['line_height']=1.25
    block['style']['layout_policy']={
        'version':VERSION,'role':kind,'decision':decision,
        'reason':'cross_block_continuation' if chunk.get('cross_block') else 'source_lines' if multiline else 'preserve_independent_region',
        'line_count':len(chunk['lines']),
        'source_block_count':len({re.search(r'-b(\d+)-',b['id']).group(1) for b in members(chunk)}),
        'source_bbox':list(block['bbox'])}


def allocate_flow_space(grouped, all_blocks, pages):
    """Grow confirmed prose into free column space, never through other content."""
    for block in grouped:
        policy=block['style'].get('layout_policy',{})
        if policy.get('decision')!='reflow' or policy.get('role')!='prose':
            continue
        page=pages[block['page_number']-1]
        original=fitz.Rect(block['bbox']);size=block['style']['size']
        peers=[other for other in all_blocks if other['id']!=block['id'] and other['page_number']==block['page_number']]
        column_right=max([original.x1]+[other['bbox'][2] for other in peers
            if abs(other['bbox'][0]-original.x0)<size*.4
            and abs(other['bbox'][1]-original.y0)<page['height']*.5
            and other['style'].get('layout_policy',{}).get('role')=='prose'])
        candidate=fitz.Rect(original)
        candidate.x1=min(page['width']-max(8,original.x0*.5),column_right)
        candidate.y1=min(page['height']-12,original.y1+max(size*1.5,original.height*.35))
        barriers=[fitz.Rect(other['bbox']) for other in peers]+visible_barriers(page)
        for obstacle in barriers:
            if obstacle.contains(original) or (obstacle.y0 < original.y0 and obstacle.y1 > original.y1
                    and (obstacle & original).get_area() > 1):
                continue  # Background panel, not an intervening boundary.
            if obstacle.x0<candidate.x1 and obstacle.x1>candidate.x0 and obstacle.y0>=original.y1-.1:
                candidate.y1=min(candidate.y1,obstacle.y0-max(2,size*.3))
            if obstacle.y0<original.y1 and obstacle.y1>original.y0 and obstacle.x0>=original.x1-.1:
                candidate.x1=min(candidate.x1,obstacle.x0-1)
        if not candidate.contains(original):
            continue
        if any((candidate & (r+(-.1,-.1,.1,.1))).get_area() > max(1,(original & (r+(-.1,-.1,.1,.1))).get_area())+.1
               for r in barriers if not r.contains(original) and not (r.y0<original.y0 and r.y1>original.y1 and (r&original).get_area()>1)):
            continue
        block['bbox']=list(candidate)
        policy['flow_bounds']=list(candidate)
