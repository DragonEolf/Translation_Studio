from pathlib import Path
from types import SimpleNamespace as N

import pymupdf as fitz
import pytest

from app.agents.checker import CheckerAgent, normalized
from app.services.ocr_notation import display_text, has_unrendered_notation
from app.services.renderer import Renderer
from app.services.text_fitter import FontRegistry, TextFitter


@pytest.mark.parametrize(
    "source,expected",
    [
        (r"API $\rightarrow$ tool", "API → tool"),
        (r"$\Rightarrow$ next", "⇒ next"),
        ("$ \\rightarrow\n$ next", "→ next"),
        (r"$$\leftarrow$$ $\leftrightarrow$ $\xi$", "← ↔ ξ"),
        (r"Cost $100 and $200; escaped \$50", r"Cost $100 and $200; escaped \$50"),
        (r"$\frac{x}{y}$", r"$\frac{x}{y}$"),
        (r"$x^2$", r"$x^2$"),
    ],
)
def test_supported_symbols_and_currency(source, expected):
    block = N(style={"ocr": True})
    assert display_text(block, source) == expected
    assert display_text(N(style={}), source) == source


@pytest.mark.parametrize(
    "text", [r"$\frac{x}{y}$", r"$\rightarrow", r"$$ (192) $", r"$x^2$"]
)
def test_unsupported_or_malformed_notation_is_flagged(text):
    assert has_unrendered_notation(N(style={"ocr": True}), text)


def test_arrow_is_visible_searchable_and_checked_as_symbol(tmp_path):
    fonts = FontRegistry(Path("fonts").resolve())
    font = fonts.resolve("hi", "native")
    if font in {None, "missing"}:
        pytest.skip("Devanagari font required")
    text = r"API $\rightarrow$ टूल $\Rightarrow$ अगला कदम $100"
    block = N(
        id="arrow",
        page_number=1,
        bbox=[20, 20, 480, 120],
        source_text=text,
        style={"ocr": True, "size": 24, "color": 0},
        confidence=0.9,
    )
    translation = N(text=text, target_language="hi", script="native")
    layout = N(**TextFitter(fonts).fit(block, text, 500, 180, [], font))
    assert layout.fit_status == "fitted"
    source, result = tmp_path / "source.pdf", tmp_path / "result.pdf"
    with fitz.open() as pdf:
        pdf.new_page(width=500, height=180)
        pdf.save(source)
    Renderer(fonts).render_page(source, 1, [(block, translation, layout)], result)
    with fitz.open(result) as pdf:
        extracted = pdf[0].get_text()
        assert normalized(extracted) == normalized("API → टूल ⇒ अगला कदम $100")
        assert "\\rightarrow" not in extracted
    assert not CheckerAgent().run(source, result, 1, [(block, translation, layout)])
    translation.text = r"$\frac{x}{y}$"
    layout = N(**TextFitter(fonts).fit(block, translation.text, 500, 180, [], font))
    Renderer(fonts).render_page(source, 1, [(block, translation, layout)], result)
    issues = CheckerAgent().run(source, result, 1, [(block, translation, layout)])
    assert "unsupported_ocr_notation" in [i["code"] for i in issues]
