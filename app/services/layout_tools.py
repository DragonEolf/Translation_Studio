"""Shared exact layout measurement for fitting, repairs and final rendering."""

from pathlib import Path

import pymupdf as fitz

from app.services.ocr_notation import display_text
from app.services.text_fitter import (
    FontRegistry,
    font_width,
    markup,
    rich_markup,
    text_direction,
)


def layout_markup(block, translation, layout, fonts=None):
    direction = text_direction(
        getattr(translation, "target_language", "en"),
        getattr(translation, "script", "native"),
    )
    path = None if layout.font in {None, "sans-serif"} else layout.font
    fonts = fonts or FontRegistry(Path(path).parent if path else None)
    runs = (
        getattr(translation, "metadata_json", {}).get("rich_text", {}).get("runs")
        if block.style.get("rich_text")
        else None
    )
    if runs is not None:
        runs = [{**run, "text": display_text(block, run["text"])} for run in runs]
        width = font_width(path) if path else "normal"
        paths = {
            bold: fonts.resolve(
                translation.target_language, translation.script, bold, width=width
            )
            for bold in (False, True)
        }
        if any(p == "missing" for p in paths.values()):
            raise ValueError("Requested font width is unavailable")
        content, css = rich_markup(
            runs,
            layout.font_size,
            block.style["size"],
            paths,
            block.style.get("align", "left"),
            direction=direction,
            registry=fonts,
            line_height=block.style.get("line_height", 1.05),
        )
    else:
        content, css = markup(
            display_text(block, translation.text),
            layout.font_size,
            block.style["color"],
            path,
            block.style.get("bold", False),
            block.style.get("align", "left"),
            block.style.get("italic", False),
            preserve_breaks=block.style.get("ocr", False),
            underline=block.style.get("underline", False),
            direction=direction,
            registry=fonts,
        )
    return content, css, fitz.Archive(str(Path(path).parent)) if path else None


def fits(block, translation, layout, fonts, page):
    try:
        content, css, archive = layout_markup(block, translation, layout, fonts)
        with fitz.open() as doc:
            target = doc.new_page(width=page.width, height=page.height)
            spare, _ = target.insert_htmlbox(
                fitz.Rect(layout.bbox), content, css=css, archive=archive, scale_low=1
            )
        return spare >= 0
    except (ValueError, RuntimeError):
        return False


def minimum_body_size(block, translation, base_size):
    """Measure body runs, excluding deliberate small superscripts/footnotes."""
    runs = (
        getattr(translation, "metadata_json", {}).get("rich_text", {}).get("runs", [])
    )
    ratios = [
        float(run["style"].get("size", block.style["size"])) / block.style["size"]
        for run in runs
        if run["text"].strip()
        and not run["style"].get("superscript")
        and float(run["style"].get("size", block.style["size"]))
        >= block.style["size"] * 0.7
    ]
    return base_size * min(ratios, default=1.0)
