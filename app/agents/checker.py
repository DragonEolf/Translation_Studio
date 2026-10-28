import re
import math
import unicodedata
import numpy as np
import pymupdf as fitz
from app.agents.translator import LITERALS
from app.services.rich_text import literal_source


def normalized(text):
    return re.sub(r"[\s\u200b-\u200d\ufeff]+", "", unicodedata.normalize("NFC",text))


class CheckerAgent:
    def run(self, source, rendered, number, entries):
        issues = []
        def add(code, message, block=None, severity="error"):
            issues.append({"code": code, "message": message, "block_id": block.id if block else None, "page_number": number, "stage": "checker", "severity":severity})
        with fitz.open(source) as original, fitz.open(rendered) as output:
            src, dst = original[number-1], output[0]
            textpage = dst.get_textpage()
            mask = np.ones((math.ceil(src.rect.height), math.ceil(src.rect.width)), dtype=bool)
            for block, translation, layout in entries:
                if block.style.get("ocr") and block.confidence < .7:
                    add("ocr_low_confidence", "OCR region detection confidence is low; verify source transcription",block)
                if layout.fit_status != "fitted":
                    messages = {
                        "signature_review_required": "Original signature region retained; isolate printed text from handwriting before translating",
                        "unsegmented_table_row": "Source row retained: separate table columns into cells before rendering translation",
                    }
                    message = messages.get(layout.fit_status, "Source text retained; manual review required")
                    add(layout.fit_status, message, block)
                    continue
                rect = fitz.Rect(layout.bbox)
                if not src.rect.contains(rect):
                    add("clipping", "Layout exceeds page boundaries", block)
                extracted = dst.get_textbox(rect + (-2, -2, 2, 2), textpage=textpage)
                if normalized(translation.text) not in normalized(extracted):
                    add("missing_text", "Rendered text differs from expected translation", block)
                for literal in LITERALS.findall(literal_source(block)):
                    if literal not in translation.text:
                        add("protected_value_changed", "A source literal is missing from translation", block)
                for region in (block.bbox, layout.bbox):
                    r = fitz.Rect(region) + (-3, -3, 3, 3)
                    mask[max(0,int(r.y0)):min(mask.shape[0],int(r.y1)+1), max(0,int(r.x0)):min(mask.shape[1],int(r.x1)+1)] = False
            fitted = [(b, l) for b, _, l in entries if l.fit_status == "fitted"]
            for i, (block, layout) in enumerate(fitted):
                for other, other_layout in fitted[i+1:]:
                    if (fitz.Rect(layout.bbox) & fitz.Rect(other_layout.bbox)).get_area() > 1:
                        add("collision", f"Layout overlaps block {other.id}", block)
            a = src.get_pixmap(alpha=False)
            b = dst.get_pixmap(alpha=False)
            arr_a = np.frombuffer(a.samples, np.uint8).reshape(a.height,a.width,a.n)
            arr_b = np.frombuffer(b.samples, np.uint8).reshape(b.height,b.width,b.n)
            if arr_a.shape == arr_b.shape:
                mask = mask[:a.height,:a.width]
                differences = np.max(np.abs(arr_a.astype(int)-arr_b.astype(int)),axis=2) > 8
                if np.count_nonzero(differences & mask) > max(10, mask.sum() * .0001):
                    add("graphics_changed", "Unexpected visual changes outside text replacement regions")
            else:
                add("page_dimensions_changed", "Output page dimensions changed")
        return issues
