"""Share geometry between nearby body paragraphs without combining translations."""

import re

import pymupdf as fitz

from app.services.cell_layout import CellLayout


def eligible(block):
    return (
        block.type == "paragraph"
        and block.style.get("rich_text")
        and not any(
            block.style.get(k)
            for k in (
                "ocr",
                "table_cell",
                "table_cell_bbox",
                "vertical",
                "superscript",
                "caption",
                "footnote",
                "list_item",
            )
        )
        and not re.match(r"^\s*(?:[●•▪*\-]|\d+[.)])\s", block.source_text)
    )


class ParagraphFlow(CellLayout):
    def apply(self, entries, blocks, page, images=()):
        available = {
            b.id: (b, t, entry_layout)
            for b, t, entry_layout in entries
            if eligible(b)
            and entry_layout.fit_status in {"fitted", "overflow"}
            and t.metadata_json.get("rich_text", {}).get("runs")
        }
        groups = []
        for block in sorted(blocks, key=lambda b: b.reading_order):
            entry = available.get(block.id)
            if entry is None:
                groups.append([])
                continue
            previous = groups[-1][-1][0] if groups and groups[-1] else None
            a = fitz.Rect(previous.bbox) if previous else None
            b = fitz.Rect(block.bbox)
            size = float(block.style["size"])
            close = (
                a is not None
                and 0 <= b.y0 - a.y1 <= size * 1.5
                and abs(a.x0 - b.x0) <= size * 0.5
                and abs(a.x1 - b.x1) <= size
                and abs(float(previous.style["size"]) - size) <= size * 0.15
            )
            if not close:
                groups.append([])
            groups[-1].append(entry)
        changed = []
        for group in groups:
            if len(group) < 2 or not any(
                entry_layout.fit_status == "overflow"
                or entry_layout.font_size < b.style["size"] * 0.75
                for b, _, entry_layout in group
            ):
                continue
            identities = {b.id for b, _, _ in group}
            bounds = fitz.Rect(group[0][0].bbox)
            for block, _, _ in group[1:]:
                bounds |= fitz.Rect(block.bbox)
            barriers = [fitz.Rect(b.bbox) for b in blocks if b.id not in identities]
            barriers += [fitz.Rect(rect) for rect in images]
            # Inflate thin rules so visible boundaries cannot disappear as empty rectangles.
            if any(
                (bounds & (r + (-0.25, -0.25, 0.25, 0.25))).get_area() > 0.1
                for r in barriers
            ):
                continue
            result = self._fit(group, bounds)
            if result is None:
                continue
            scale, heights, gap = result
            cursor = bounds.y0
            for (block, translation, layout), height in zip(group, heights):
                layout.bbox = [bounds.x0, cursor, bounds.x1, cursor + height]
                layout.font_size = float(block.style["size"]) * scale
                layout.fit_status = "fitted"
                layout.line_breaks = translation.text.splitlines()
                cursor += height + gap
                changed.append(block.id)
        return changed
