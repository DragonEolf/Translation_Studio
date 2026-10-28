"""Associate simple PDF underline paths with their owning text spans."""
import pymupdf as fitz


def filled_rectangle(drawing):
    """Accept a rectangle operator or its equivalent closed polygon.

    Office exporters sometimes draw hyperlink underlines as six edges, with
    extra vertices halfway along the top and bottom. Restrict detection to a
    closed rectangular perimeter so unrelated filled artwork is left alone.
    """
    items = drawing['items']
    if drawing['type'] != 'f' or not items:
        return False
    if len(items) == 1 and items[0][0] == 're':
        return True
    box = fitz.Rect(drawing['rect'])
    if not all(item[0] == 'l' for item in items):
        return False
    tolerance = .01
    length = 0.
    for index, (_, start, end) in enumerate(items):
        following = items[(index + 1) % len(items)][1]
        if abs(end - following) > tolerance:
            return False
        horizontal = abs(start.y-end.y) < tolerance and any(
            abs(start.y-y) < tolerance for y in (box.y0, box.y1))
        vertical = abs(start.x-end.x) < tolerance and any(
            abs(start.x-x) < tolerance for x in (box.x0, box.x1))
        if not (horizontal or vertical):
            return False
        length += abs(end-start)
    return abs(length - 2*(box.width+box.height)) < .1


def attach_underlines(page, data):
    """Keep decoration geometry separate from style and ignore structural rules.

    MuPDF detects underlined characters with TEXT_COLLECT_STYLES. Only simple,
    thin horizontal paths covered by those characters are eligible for removal.
    A path shared across runs is removed only when every owner is replaced.
    """
    spans = [s for b in data['blocks'] if b['type'] == 0
             for line in b['lines'] for s in line['spans']
             if s.get('char_flags', 0) & 2 and s['text'].strip()]
    for drawing in page.get_drawings() if spans else []:
        items = drawing['items']
        horizontal = (drawing['type'] == 's' and items and
                      all(i[0] == 'l' and abs(i[1].y-i[2].y) < .01 for i in items))
        filled_rule = filled_rectangle(drawing)
        if not (horizontal or filled_rule):
            continue
        box = fitz.Rect(drawing['rect'])
        thickness = max(box.height, drawing.get('width') or 0)
        if box.width <= 0 or thickness > 3 or box.height > 3:
            continue
        owners = [s for s in spans
                  if s['origin'][1] <= (box.y0 + box.y1) / 2 <= s['origin'][1] + s['size'] * .3
                  and min(box.x1, s['bbox'][2]) > max(box.x0, s['bbox'][0])]
        intervals = sorted((max(box.x0, s['bbox'][0]), min(box.x1, s['bbox'][2])) for s in owners)
        end = box.x0
        covered = 0
        for left, right in intervals:
            covered += max(0, right - max(left, end))
            end = max(end, right)
        if covered < box.width - 1:
            continue
        pad = max(.1, (drawing.get('width') or 0) / 2 + .1)
        path = {'bbox': list(box + (-pad, -pad, pad, pad)),
                'owners': [list(s['bbox']) for s in owners]}
        for span in owners:
            span.setdefault('underline_paths', []).append(path)
    # TEXT_COLLECT_STYLES can mistake nearby table borders for underlines.
    # Trust it only when we also found an owned, removable decoration path.
    for span in spans:
        if not span.get('underline_paths'):
            span['char_flags'] &= ~2


def replaced_underlines(entries):
    """Return old underline regions only when all source owners will render."""
    regions = [region for b, _, layout in entries if layout.fit_status == 'fitted'
               for region in b.style.get('source_regions', [b.bbox])]
    paths = {}
    for block, _, layout in entries:
        if layout.fit_status != 'fitted':
            continue
        for path in block.style.get('underline_paths', []):
            if all(any(all(abs(a-b) < .01 for a, b in zip(owner, region))
                       for region in regions) for owner in path['owners']):
                paths[tuple(path['bbox'])] = path['bbox']
    return list(paths.values())
