"""Choose supported fonts and fit translations while retaining unsafe source regions."""

import re

from app.services.ocr_notation import display_text
from app.services.text_fitter import TextFitter, text_direction


def contains_signature(block):
    """Conservatively retain OCR crops that may contain a handwritten signature.

    OCR text is insufficient to isolate handwriting from its printed label.
    This also covers existing saved blocks, without another OCR call.
    """
    return bool(
        block.style.get("ocr")
        and (
            block.style.get("signature")
            or re.search(r"\b(signatures?|signed\s+by)\b", block.source_text, re.I)
        )
    )


class ReorganiserAgent:
    def __init__(self, fonts):
        self.fonts = fonts
        self.fitter = TextFitter(fonts)

    def run(self, block, translation, page, obstacles):
        if block.style.get("ocr_overlap_with"):
            return {
                "bbox": block.bbox,
                "font": "sans-serif",
                "font_size": block.style["size"],
                "line_breaks": [],
                "fit_status": "ocr_overlap_review_required",
            }
        if contains_signature(block):
            return {
                "bbox": block.bbox,
                "font": "sans-serif",
                "font_size": block.style["size"],
                "line_breaks": [],
                "fit_status": "signature_review_required",
            }
        if block.style.get("unsegmented_table_row"):
            return {
                "bbox": block.bbox,
                "font": "sans-serif",
                "font_size": block.style["size"],
                "line_breaks": [],
                "fit_status": "unsegmented_table_row",
            }
        font = self.fonts.resolve(
            translation.target_language,
            translation.script,
            block.style.get("bold", False),
        )
        unsupported = (
            block.style.get("vertical")
            or block.style.get("complex_table")
            or block.style.get("unsupported_ocr")
        )
        if not self.fonts.supports(
            display_text(block, translation.text), font, block.style.get("bold", False)
        ):
            font = "missing"
        if font == "missing" or unsupported:
            return {
                "bbox": block.bbox,
                "font": font or "sans-serif",
                "font_size": block.style["size"],
                "line_breaks": [],
                "fit_status": "unsupported_glyphs"
                if font == "missing"
                else "unsupported_layout",
            }
        runs = (
            getattr(translation, "metadata_json", {}).get("rich_text", {}).get("runs")
            if block.style.get("rich_text")
            else None
        )
        rich_fonts = (
            {
                bold: self.fonts.resolve(
                    translation.target_language, translation.script, bold
                )
                for bold in (False, True)
            }
            if runs is not None
            else None
        )
        if runs is not None:
            for run in runs:
                path = rich_fonts[bool(run["style"].get("bold"))]
                if not self.fonts.supports(
                    run["text"], path, bool(run["style"].get("bold"))
                ):
                    return {
                        "bbox": block.bbox,
                        "font": font or "sans-serif",
                        "font_size": block.style["size"],
                        "line_breaks": [],
                        "fit_status": "unsupported_glyphs",
                    }
        direction = text_direction(translation.target_language, translation.script)
        result = self.fitter.fit(
            block,
            translation.text,
            page.width,
            page.height,
            obstacles,
            font,
            runs,
            rich_fonts,
            direction=direction,
        )
        if result["fit_status"] == "overflow" and runs is None:
            condensed = self.fonts.resolve(
                translation.target_language,
                translation.script,
                block.style.get("bold", False),
                True,
            )
            if condensed not in {font, None, "missing"}:
                result = self.fitter.fit(
                    block,
                    translation.text,
                    page.width,
                    page.height,
                    obstacles,
                    condensed,
                    direction=direction,
                )
        return result
