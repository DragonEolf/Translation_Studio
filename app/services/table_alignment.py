"""Preserve source-centred table cells using measured, shaped text geometry."""
from collections import defaultdict
from types import SimpleNamespace
import pymupdf as fitz
from app.services.layout_tools import layout_markup


def union(rects):
    result = fitz.Rect(rects[0])
    for rect in rects[1:]:
        result |= fitz.Rect(rect)
    return result


def cell_groups(entries):
    cells = defaultdict(list)
    for entry in entries:
        block = entry[0]
        cell = block.style.get('table_cell_bbox')
        if cell:
            cells[tuple(cell)].append(entry)
    return cells.items()


def source_centering(source_page, cell):
    """Require positive source evidence; do not guess from translated text."""
    rect = fitz.Rect(cell)
    lines = []
    for block in source_page.get_text('dict', clip=rect)['blocks']:
        for line in block.get('lines', []):
            spans = [s for s in line['spans'] if s['text'].strip()]
            if spans:
                lines.append(union([s['bbox'] for s in spans]))
    if not lines:
        return False, False
    tolerance = min(4., max(1.5, rect.height * .02))
    horizontal = all(abs((line.x0 + line.x1 - rect.x0 - rect.x1) / 2) <= tolerance
                     for line in lines)
    bounds = union(lines)
    vertical = (abs((bounds.y0 + bounds.y1 - rect.y0 - rect.y1) / 2) <= tolerance
                and rect.height - bounds.height > tolerance * 2)
    return horizontal, vertical


def visible_lines(entry, page_rect, fonts=None, compact=False):
    """Measure the same shaped content used by Renderer, before outlining Indic.

    The final Indic PDF's ActualText is a semantic box, not glyph geometry.
    Measuring its pre-outline rendering avoids treating that box as visible ink.
    """
    block, translation, layout = entry
    content, css, archive = layout_markup(block, translation, layout, fonts)
    with fitz.open() as doc:
        page = doc.new_page(width=page_rect.width, height=page_rect.height)
        spare, _ = page.insert_htmlbox(fitz.Rect(layout.bbox), content, css=css,
                                       archive=archive, scale_low=1)
        if spare < 0:
            return []
        if compact:
            layout.bbox = [*layout.bbox[:3], layout.bbox[3] - max(0, spare - .1)]
        lines = [union([s['bbox'] for s in line['spans'] if s['text'].strip()])
                 for b in page.get_text('dict')['blocks'] for line in b.get('lines', [])
                 if any(s['text'].strip() for s in line['spans'])]
        # MuPDF can split one shaped Indic line into overlapping extraction
        # lines at a reordered vowel/ligature. Judge their combined geometry.
        merged = []
        for line in lines:
            peer = next((r for r in merged if abs(r.y0-line.y0) < .5
                         and abs(r.y1-line.y1) < .5), None)
            if peer is None:
                merged.append(line)
            else:
                peer.include_rect(line)
        return merged


class TableAlignment:
    def __init__(self, fonts=None):
        self.fonts = fonts

    def diagnostics(self, source_page, entries):
        warnings = []
        for cell, group in cell_groups(entries):
            if any(l.fit_status != 'fitted' or b.style.get('ocr') or b.style.get('vertical')
                   for b, _, l in group):
                continue
            horizontal, vertical = source_centering(source_page, cell)
            if not (horizontal or vertical):
                continue
            measured = [(entry, visible_lines(entry, source_page.rect, self.fonts)) for entry in group]
            if not all(lines for _, lines in measured):
                continue
            rect = fitz.Rect(cell)
            for (block, _, _), lines in measured:
                if horizontal and any(abs((r.x0+r.x1-rect.x0-rect.x1)/2) > 2 for r in lines):
                    warnings.append((block, 'table_horizontal_alignment',
                                     'Source cell is horizontally centered; translated lines are off center.'))
            bounds = union([r for _, lines in measured for r in lines])
            if vertical and abs((bounds.y0+bounds.y1-rect.y0-rect.y1)/2) > 2:
                warnings.append((group[0][0], 'table_vertical_alignment',
                                 'Source cell is vertically centered; translated content is off center.'))
        return warnings

    def apply(self, source_page, entries, blocks, images=()):
        changed = []
        for cell, group in cell_groups(entries):
            ids = {b.id for b, _, _ in group}
            # A retained passage or an image prevents moving the cell as a unit.
            if any(b.style.get('table_cell_bbox') == list(cell) and b.id not in ids for b in blocks):
                continue
            if any(l.fit_status != 'fitted' or b.style.get('ocr') or b.style.get('vertical')
                   for b, _, l in group):
                continue
            horizontal, vertical = source_centering(source_page, cell)
            if not (horizontal or vertical):
                continue
            rect = fitz.Rect(cell) & source_page.rect
            if any((rect & fitz.Rect(image)).get_area() > 1 for image in images):
                continue
            candidates = []
            for block, translation, layout in group:
                candidate = SimpleNamespace(bbox=list(layout.bbox), font=layout.font,
                                            font_size=layout.font_size, fit_status=layout.fit_status)
                styled = SimpleNamespace(id=block.id, bbox=block.bbox, style=dict(block.style))
                if horizontal:
                    styled.style['align'] = 'center'
                    candidate.bbox[0], candidate.bbox[2] = rect.x0+1, rect.x1-1
                candidates.append((styled, translation, candidate))
            measured = [visible_lines(e, source_page.rect, self.fonts, compact=vertical) for e in candidates]
            if not all(measured):
                continue
            dy = 0
            if vertical:
                bounds = union([r for lines in measured for r in lines])
                dy = (rect.y0+rect.y1-bounds.y0-bounds.y1)/2
            for _, _, layout in candidates:
                layout.bbox[1] += dy
                layout.bbox[3] += dy
            # Check allocated boxes, not only glyphs, for page/cell containment
            # and obstacles. Existing inter-paragraph spacing is preserved.
            obstacles = [fitz.Rect(b.bbox) for b in blocks if b.id not in ids]
            boxes = [fitz.Rect(l.bbox) for _, _, l in candidates]
            if any(not rect.contains(box) or any((box & other).get_area() > 1 for other in obstacles)
                   for box in boxes):
                continue
            if any((box & other).get_area() > 1 for i, box in enumerate(boxes) for other in boxes[i+1:]):
                continue
            for (block, _, layout), (styled, _, candidate) in zip(group, candidates):
                if layout.bbox != candidate.bbox or block.style != styled.style:
                    layout.bbox = candidate.bbox
                    block.style = styled.style
                    changed.append(block.id)
        return changed
