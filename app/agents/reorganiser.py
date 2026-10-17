from app.services.text_fitter import TextFitter
import pymupdf as fitz
import re


def contains_signature(block):
    """Conservatively retain OCR crops that may contain a handwritten signature.

    OCR text is insufficient to isolate handwriting from its printed label.
    This also covers existing saved blocks, without another OCR call.
    """
    return bool(block.style.get("ocr") and (
        block.style.get("signature") or
        re.search(r"\b(signatures?|signed\s+by)\b", block.source_text, re.I)
    ))


class ReorganiserAgent:
    def __init__(self, fonts):
        self.fonts = fonts
        self.fitter = TextFitter()

    def run(self, block, translation, page, obstacles):
        if contains_signature(block):
            return {"bbox": block.bbox, "font": "sans-serif", "font_size": block.style["size"], "line_breaks": [], "fit_status": "signature_review_required"}
        if block.style.get("unsegmented_table_row"):
            return {"bbox": block.bbox, "font": "sans-serif", "font_size": block.style["size"], "line_breaks": [], "fit_status": "unsegmented_table_row"}
        font = self.fonts.resolve(translation.target_language, translation.script,block.style.get("bold",False))
        unsupported = block.style.get("vertical") or block.style.get("complex_table") or block.style.get("unsupported_ocr")
        if font not in {None,"missing"}:
            chosen = fitz.Font(fontfile=font)
            fallback_fonts = (fitz.Font("helv"),fitz.Font("zapfdingbats"),fitz.Font("symb"))
            if any(not chosen.has_glyph(ord(c)) and not any(f.has_glyph(ord(c)) for f in fallback_fonts) for c in translation.text if not c.isspace() and c not in "\u200c\u200d"):
                font = "missing"
        elif font is None:
            chosen = fitz.Font("helv")
            if any(not chosen.has_glyph(ord(c)) for c in translation.text if not c.isspace()):
                font = "missing"
        if font == "missing" or unsupported:
            return {"bbox": block.bbox, "font": font or "sans-serif", "font_size": block.style["size"], "line_breaks": [], "fit_status": "unsupported_glyphs" if font == "missing" else "unsupported_layout"}
        result = self.fitter.fit(block, translation.text, page.width, page.height, obstacles, font)
        if result["fit_status"] == "overflow":
            condensed = self.fonts.resolve(translation.target_language,translation.script,block.style.get("bold",False),True)
            if condensed not in {font,None,"missing"}:
                result = self.fitter.fit(block, translation.text,page.width,page.height,obstacles,condensed)
        return result
