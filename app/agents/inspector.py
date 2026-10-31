"""Validate PDF inputs and choose digital, scanned, or blank-page extraction."""

import pymupdf as fitz

from app.config import StudioError


class InspectorAgent:
    def run(self, path, max_pages=100):
        try:
            pdf = fitz.open(path)
        except Exception:
            raise StudioError("invalid_pdf", "The file is not a readable PDF")
        with pdf:
            if pdf.is_encrypted:
                raise StudioError("encrypted_pdf", "Encrypted PDFs are unsupported")
            if not pdf.is_pdf or not len(pdf):
                raise StudioError("invalid_pdf", "Empty or invalid PDF")
            if len(pdf) > max_pages:
                raise StudioError("too_many_pages", f"Maximum {max_pages} pages")
            result = []
            for page in pdf:
                has_text = bool(page.get_text().strip())
                images = len(page.get_images())
                visual_content = images or bool(page.get_drawings())
                result.append(
                    {
                        "number": page.number + 1,
                        "width": page.rect.width,
                        "height": page.rect.height,
                        "fonts": [font[3] for font in page.get_fonts()],
                        "text_coverage": sum(
                            fitz.Rect(b[:4]).get_area()
                            for b in page.get_text("blocks")
                            if b[6] == 0
                        )
                        / page.rect.get_area(),
                        "images": images,
                        "extraction_method": "digital"
                        if has_text
                        else "ocr"
                        if visual_content
                        else "blank",
                    }
                )
            return result
