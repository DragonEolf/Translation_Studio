"""Classify extracted spans while retaining stable IDs, styles, and source geometry."""

import re
from collections import Counter
from statistics import median

import pymupdf as fitz

from app.services.rich_text import source_alignment


def inside(span, bbox):
    r = fitz.Rect(span["bbox"])
    return fitz.Rect(bbox).contains((r.tl + r.br) / 2)


def span_style(spans):
    # Dominant text style, not the largest span touching a neighboring row.
    weights = Counter()
    for s in spans:
        weights[(s["font"], round(s["size"], 2), s["color"], s["flags"])] += max(
            1, len(s["text"].strip())
        )
    font, size, color, flags = weights.most_common(1)[0][0]
    return {
        "font": font,
        "size": size,
        "color": color,
        "bold": bool(flags & 16)
        or bool(
            re.search(
                r"(?:^|[-_,])(?:Bold|SemiBold|Demi|Heavy|Black)(?:Italic|Oblique|Cond|Cn|MT|PS|$)",
                font,
                re.I,
            )
        ),
        "italic": bool(flags & 2),
        "superscript": bool(flags & 1),
        "underline": any(s.get("char_flags", 0) & 2 for s in spans),
    }


class BlockMakerAgent:
    def run(self, document_id, pages):
        blocks = []
        for number, page in enumerate(pages, 1):
            raw = [b for b in page["blocks"] if b["type"] == 0]
            sizes = [
                s["size"] for b in raw for line in b["lines"] for s in line["spans"]
            ]
            body_size = median(sizes) if sizes else 12
            cells = [
                cell for table in page.get("tables", []) for cell in table["cells"]
            ]
            for block_index, block in enumerate(raw):
                for line_index, line in enumerate(block["lines"]):
                    for span_index, span in enumerate(line["spans"]):
                        text = span["text"].strip()
                        if not text:
                            continue
                        box = list(span.get("ink_bbox", span["bbox"]))
                        cell = next((c for c in cells if inside(span, c["bbox"])), None)
                        kind = "paragraph"
                        if cell:
                            kind = "table_cell"
                        elif span["size"] >= body_size * 1.2:
                            kind = "heading"
                        elif re.match(r"^(?:[•▪*-]|\d+[.)])\s", text):
                            kind = "list_item"
                        elif re.match(r"^(?:Figure|Table|Fig\.)\s+\d", text, re.I):
                            kind = "caption"
                        elif box[1] < page["height"] * 0.05:
                            kind = "header"
                        elif box[3] > page["height"] * 0.95:
                            kind = "footer"
                        elif (
                            span["size"] < body_size * 0.85
                            and box[1] > page["height"] * 0.7
                        ):
                            kind = "footnote"
                        style = {
                            **span_style([span]),
                            "source_regions": [list(span["bbox"])],
                            "underline_paths": span.get("underline_paths", []),
                            "digital_span": True,
                            "fixed_box": True,
                            "align": "left",
                            "vertical": abs(line["dir"][0] - 1) > 0.01
                            or abs(line["dir"][1]) > 0.01,
                        }
                        if cell:
                            style.update(table_cell=True, table_cell_bbox=cell["bbox"])
                        # A single-span line has no inline neighbours whose
                        # relative positions could be changed by centring it.
                        if len([s for s in line["spans"] if s["text"].strip()]) == 1:
                            style["align"] = source_alignment(
                                [
                                    {
                                        "text": text,
                                        "bbox": box,
                                        "cell": cell["bbox"] if cell else (),
                                    }
                                ],
                                style,
                                page,
                            )
                        blocks.append(
                            {
                                "id": f"{document_id}-p{number}-b{block_index}-l{line_index}-s{span_index}",
                                "document_id": document_id,
                                "page_number": number,
                                "type": kind,
                                "reading_order": len(blocks),
                                "bbox": box,
                                "source_text": text,
                                "style": style,
                                "confidence": 0.8,
                            }
                        )
        # Stable spatial ordering groups left/right columns when a broad gutter exists.
        for number in range(1, len(pages) + 1):
            items = [b for b in blocks if b["page_number"] == number]
            width = pages[number - 1]["width"]
            narrow = [
                b
                for b in items
                if b["bbox"][2] - b["bbox"][0] < width * 0.55
                and b["type"] not in {"header", "footer", "table_cell"}
            ]
            two_columns = any(b["bbox"][2] < width * 0.55 for b in narrow) and any(
                b["bbox"][0] > width * 0.45 for b in narrow
            )
            key = (
                (
                    lambda b: (
                        0 if b["bbox"][2] < width * 0.55 else 1,
                        b["bbox"][1],
                        b["bbox"][0],
                    )
                )
                if two_columns
                else (lambda b: (b["bbox"][1], b["bbox"][0]))
            )
            for order, item in enumerate(sorted(items, key=key)):
                item["reading_order"] = order
        return blocks
