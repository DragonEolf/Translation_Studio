"""Shared typography and vertical flow for related paragraphs in table cells."""
from collections import defaultdict
import math
import pymupdf as fitz
from app.services.text_fitter import rich_markup, text_direction


class CellLayout:
    def __init__(self, fonts, min_size=6):
        self.fonts = fonts
        self.min_size = min_size

    def apply(self, entries, blocks, page, images=()):
        """Pool cell space and apply one scale to the original font sizes.

        Only translated rich-text paragraphs participate. Missing/unsupported
        paragraphs, columns, and cell borders are barriers. Different original
        sizes retain their ratios under the common cell scale.
        Layouts are updated atomically after a complete shared fit succeeds.
        """
        available = {b.id: (b, t, layout) for b, t, layout in entries}
        cells = defaultdict(list)
        for block in blocks:
            cell = block.style.get('table_cell_bbox')
            if cell:
                cells[tuple(cell)].append(block)
        changed = []
        for cell, members in cells.items():
            members.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
            groups = []
            current = []
            for block in members:
                entry = available.get(block.id)
                eligible = entry and entry[2].fit_status in {'fitted', 'overflow'} and block.style.get('rich_text') and getattr(entry[1], 'metadata_json', {}).get('rich_text', {}).get('runs')
                if not eligible:
                    if current:
                        groups.append(current)
                        current = []
                    continue
                if current:
                    previous = current[-1][0]
                    below = block.bbox[1] > previous.bbox[1] + min(block.style['size'], previous.style['size']) * .5
                    if not below:
                        groups.append(current)
                        current = []
                current.append(entry)
            if current:
                groups.append(current)
            for group in groups:
                if len(group) < 2 and group[0][2].fit_status != "overflow":
                    continue
                identities = {b.id for b, _, _ in group}
                bounds = fitz.Rect(cell) & fitz.Rect(0, 0, page.width, page.height)
                bounds += (.75, .75, -.75, -.75)
                # Retain the original paragraph indent, including bullet markers.
                if not all(b.style.get('align') == 'center' for b, _, _ in group):
                    bounds.x0 = max(bounds.x0, min(b.bbox[0] for b, _, _ in group))
                top = min(b.bbox[1] for b, _, _ in group)
                bottom = max(b.bbox[3] for b, _, _ in group)
                for other in members:
                    if other.id in identities:
                        continue
                    rect = fitz.Rect(available[other.id][2].bbox if other.id in available else other.bbox)
                    if rect.y1 <= top + .1:
                        bounds.y0 = max(bounds.y0, rect.y1 + .5)
                    elif rect.y0 >= bottom - .1:
                        bounds.y1 = min(bounds.y1, rect.y0 - .5)
                if bounds.is_empty:
                    continue
                # Do not flow through images or text excluded from the group.
                barriers = [fitz.Rect(b.bbox) for b in blocks if b.id not in identities]
                barriers += [fitz.Rect(rect) for rect in images]
                if any((bounds & rect).get_area() > 1 for rect in barriers):
                    continue
                result = self._fit(group, bounds)
                if result is None:
                    continue
                scale, heights, gap = result
                cursor = bounds.y0
                for (block, translation, layout), height in zip(group, heights):
                    layout.bbox = [bounds.x0, cursor, bounds.x1, cursor + height]
                    layout.font_size = float(block.style['size']) * scale
                    layout.fit_status = "fitted"
                    layout.line_breaks = translation.text.splitlines()
                    cursor += height + gap
                    changed.append(block.id)
        return changed

    def _measure(self, group, bounds, scale):
        gap = max(.3, min(float(b.style['size']) for b, _, _ in group) * scale * .12)
        heights = []
        measure_height = max(4096, bounds.height * 4)
        with fitz.open() as doc:
            for block, translation, _ in group:
                runs = translation.metadata_json['rich_text']['runs']
                fonts = {bold: self.fonts.resolve(translation.target_language, translation.script, bold) for bold in (False, True)}
                size = float(block.style['size']) * scale
                content, css = rich_markup(runs, size, float(block.style['size']), fonts, block.style.get('align', 'left'), direction=text_direction(translation.target_language, translation.script), registry=self.fonts, line_height=block.style.get("line_height",1.05))
                page = doc.new_page(width=max(72, bounds.width + 2), height=measure_height + 2)
                spare, _ = page.insert_htmlbox(fitz.Rect(0, 0, bounds.width, measure_height), content, css=css, scale_low=1)
                if spare < 0:
                    return None
                # Round upwards so final per-paragraph rendering has spare room.
                height = math.ceil((measure_height - spare) * 100) / 100 + .05
                heights.append(height)
        if sum(heights) + gap * (len(group) - 1) > bounds.height:
            return None
        return heights, gap

    def _fit(self, group, bounds):
        # Enforce a readable floor across all body runs in the cell.
        from app.services.layout_tools import minimum_body_size
        low = max(min(self.min_size, float(b.style["size"])) /
                  max(.01, minimum_body_size(b, t, float(b.style["size"])))
                  for b, t, _ in group)
        if low > 1:
            return None
        measured = self._measure(group, bounds, low)
        if measured is None:
            return None
        high = 1.
        best = (low, *measured)
        for attempt in range(16):
            size = high if attempt == 0 else (low + high) / 2
            measured = self._measure(group, bounds, size)
            if measured is None:
                high = size
            else:
                heights, gap = measured
                best = (size, heights, gap)
                low = size
                if attempt == 0:
                    break
        return best
