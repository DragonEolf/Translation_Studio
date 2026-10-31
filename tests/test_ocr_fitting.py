import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from app.agents.checker import normalized
from app.agents.ocr_reader import OCRReaderAgent
from app.services.layout_tools import fits
from app.services.renderer import Renderer
from app.services.text_fitter import FontRegistry, TextFitter


def test_novelty_translation_fits_and_renders_in_full(tmp_path):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/ocr_novelty.json").read_text()
    )
    fonts = FontRegistry(Path("fonts").resolve())
    font = fonts.resolve("ta", "native")
    if font in {None, "missing"}:
        pytest.skip("Tamil Noto fonts are required")
    block = SimpleNamespace(id="novelty", bbox=fixture["bbox"], style=fixture["style"])
    translation = SimpleNamespace(
        text=fixture["translation"], target_language="ta", script="native"
    )
    width, height = fixture["page_width"], fixture["page_height"]
    layout = SimpleNamespace(
        **TextFitter(fonts).fit(block, translation.text, width, height, [], font)
    )
    assert layout.fit_status == "fitted"
    assert 8 <= layout.font_size < 16.8  # Previously search stopped at 16.8pt.
    assert layout.bbox == block.bbox
    page = SimpleNamespace(width=width, height=height)
    assert fits(block, translation, layout, fonts, page)
    larger = SimpleNamespace(**{**vars(layout), "font_size": layout.font_size + 0.05})
    assert not fits(block, translation, larger, fonts, page)
    source, rendered = tmp_path / "scan.pdf", tmp_path / "translated.pdf"
    with fitz.open() as pdf:
        pdf.new_page(width=width, height=height)
        pdf.save(source)
    Renderer(fonts).render_page(source, 1, [(block, translation, layout)], rendered)
    with fitz.open(rendered) as pdf:
        assert normalized(pdf[0].get_textbox(fitz.Rect(block.bbox))) == normalized(
            translation.text
        )
        assert normalized(pdf[0].get_text()) == normalized(translation.text)


def test_ocr_retains_source_when_even_eight_points_cannot_fit():
    block = SimpleNamespace(
        bbox=[10, 10, 30, 20], style={"ocr": True, "size": 24, "color": 0}
    )
    result = TextFitter().fit(
        block, "A long paragraph that cannot fit. " * 50, 200, 200, [], None
    )
    assert result["fit_status"] == "overflow"
    assert result["bbox"] == block.bbox
    assert result["line_breaks"] == []


def test_ocr_font_ceiling_does_not_depend_on_provider_newlines():
    sizes = []
    for text in [
        "The entire handwritten paragraph on one line.",
        "The entire\nhandwritten\nparagraph\non one line.",
    ]:
        response = {
            "choices": [{"finish_reason": "stop"}],
            "blocks": [{"bbox_xyxy": [10, 10, 180, 190], "text": text}],
        }
        block = OCRReaderAgent().blocks(
            "doc", 1, {"width": 200, "height": 200}, response, 200, 200
        )[0]
        sizes.append(block["style"]["size"])
    assert sizes == [24, 24]
