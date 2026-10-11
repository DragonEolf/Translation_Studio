import re
from statistics import median
import pymupdf as fitz
from collections import Counter


def inside(span, bbox):
    r = fitz.Rect(span["bbox"])
    return fitz.Rect(bbox).contains((r.tl+r.br)/2)


def span_style(spans):
    # Dominant text style, not the largest span touching a neighboring row.
    weights = Counter()
    for s in spans:
        weights[(s["font"],round(s["size"],2),s["color"],s["flags"])] += max(1,len(s["text"].strip()))
    font,size,color,flags = weights.most_common(1)[0][0]
    return {"font":font,"size":size,"color":color,"bold":bool(flags & 16),"italic":bool(flags & 2)}


class BlockMakerAgent:
    def run(self, document_id, pages):
        blocks = []
        for number, page in enumerate(pages, 1):
            raw = [b for b in page["blocks"] if b["type"] == 0]
            sizes = [s["size"] for b in raw for line in b["lines"] for s in line["spans"]]
            body_size = median(sizes) if sizes else 12
            cells = [cell for table in page.get("tables",[]) for cell in table["cells"]]
            for order, block in enumerate(raw):
                lines = [{**line,"spans":[s for s in line["spans"] if not any(inside(s,cell["bbox"]) for cell in cells)]} for line in block["lines"]]
                lines = [line for line in lines if any(s["text"].strip() for s in line["spans"])]
                if not lines:
                    continue
                spans = [s for line in lines for s in line["spans"] if s["text"].strip()]
                text = "\n".join("".join(s["text"] for s in line["spans"]) for line in lines).strip()
                if not text:
                    continue
                largest = max(spans, key=lambda s: s["size"])
                kind = "paragraph"
                if largest["size"] >= body_size * 1.2:
                    kind = "heading"
                elif re.match(r"^(?:[•▪*-]|\d+[.)])\s", text):
                    kind = "list_item"
                elif re.match(r"^(?:Figure|Table|Fig\.)\s+\d", text, re.I):
                    kind = "caption"
                elif block["bbox"][1] < page["height"] * .05:
                    kind = "header"
                elif block["bbox"][3] > page["height"] * .95:
                    kind = "footer"
                elif largest["size"] < body_size * .85 and block["bbox"][1] > page["height"] * .7:
                    kind = "footnote"
                box = fitz.Rect(spans[0]["bbox"])
                for s in spans[1:]:
                    box |= fitz.Rect(s["bbox"])
                blocks.append({"id": f"{document_id}-p{number}-b{order}", "document_id": document_id, "page_number": number, "type": kind, "reading_order": order, "bbox": list(box), "source_text": text, "style": {**span_style(spans),"source_regions":[list(s["bbox"]) for s in spans], "vertical": any(abs(line["dir"][0]-1)>.01 or abs(line["dir"][1])>.01 for line in lines)}, "confidence": .8})
            for table_index, table in enumerate(page.get("tables", [])):
                for cell_index, cell in enumerate(table["cells"]):
                    related = [s for b in raw for line in b["lines"] for s in line["spans"] if s["text"].strip() and inside(s,cell["bbox"])]
                    style = span_style(related) if related else {"size":body_size,"font":"unknown","color":0}
                    box = fitz.Rect(cell["bbox"]) + (.8,.5,-.8,-.5)
                    # Merged cells still have usable rectangles: render each detected cell.
                    blocks.append({"id":f"{document_id}-p{number}-t{table_index}-c{cell_index}", "document_id":document_id,"page_number":number,"type":"table_cell","reading_order":len(raw)+table_index*1000+cell_index,"bbox":list(box),"source_text":cell["text"],"style":{**style,"source_regions":[list(s["bbox"]) for s in related],"table_cell":True,"align":"center" if box.width < 240 or style.get("color")==16777215 else "left"},"confidence":.9})
        # Stable spatial ordering groups left/right columns when a broad gutter exists.
        for number in range(1,len(pages)+1):
            items = [b for b in blocks if b["page_number"] == number]
            width = pages[number-1]["width"]
            narrow = [b for b in items if b["bbox"][2]-b["bbox"][0] < width*.55 and b["type"] not in {"header","footer","table_cell"}]
            two_columns = any(b["bbox"][2] < width*.55 for b in narrow) and any(b["bbox"][0] > width*.45 for b in narrow)
            key = (lambda b:(0 if b["bbox"][2] < width*.55 else 1,b["bbox"][1],b["bbox"][0])) if two_columns else (lambda b:(b["bbox"][1],b["bbox"][0]))
            for order, item in enumerate(sorted(items,key=key)):
                item["reading_order"] = order
        return blocks
