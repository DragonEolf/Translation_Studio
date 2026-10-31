from pathlib import Path
from types import SimpleNamespace as N

import pymupdf as fitz
import pytest

from app.agents.reorganiser import ReorganiserAgent
from app.services.layout_tools import fits, layout_markup
from app.services.renderer import Renderer
from app.services.text_fitter import FontRegistry, fallback_markup


@pytest.mark.parametrize("rich", [False, True])
def test_currency_only_uses_fallback_and_renders_translated_line(tmp_path, rich):
    fonts = FontRegistry(Path("fonts").resolve())
    text = "فڪسڊ پي: ₹15.30 L"
    style = {"size": 14, "color": 0, "fixed_box": True, "bold": True}
    if rich:
        style["rich_text"] = {"version": 1}
    runs = [{"text": text, "style": {"size": 14, "bold": True, "underline": True}}]
    b = N(bbox=[30, 30, 360, 120], style=style)
    t = N(
        text=text,
        target_language="sd-Arab",
        script="native",
        metadata_json={"rich_text": {"runs": runs}} if rich else {},
    )
    page = N(width=400, height=200)
    entry_layout = N(**ReorganiserAgent(fonts).run(b, t, page, []))
    assert entry_layout.fit_status == "fitted"
    replacements = fonts.glyph_fallbacks(text, entry_layout.font, True)
    assert "₹" in replacements
    assert all(
        not fitz.Font(fontfile=entry_layout.font).has_glyph(ord(c))
        for c in replacements
    )
    assert Path(replacements["₹"]).name == "NotoSans-Bold.ttf"
    assert fits(b, t, entry_layout, fonts, page)
    content, css, archive = layout_markup(b, t, entry_layout, fonts)
    assert ">₹</span>" in content
    with fitz.open() as doc:
        p = doc.new_page(width=400, height=200)
        assert (
            p.insert_htmlbox(
                entry_layout.bbox, content, css=css, archive=archive, scale_low=1
            )[0]
            >= 0
        )
        currency = [
            s
            for block in p.get_text("dict")["blocks"]
            if "lines" in block
            for line in block["lines"]
            for s in line["spans"]
            if "₹" in s["text"]
        ]
        assert currency and "NotoSans-Bold" in currency[0]["font"]
        assert currency[0]["text"] == "₹"
    source = tmp_path / "source.pdf"
    with fitz.open() as doc:
        doc.new_page(width=400, height=200).insert_text(
            (30, 50), "Fixed Pay: Rs 15.30 L"
        )
        doc.save(source)
    Renderer(fonts).render_page(
        source, 1, [(b, t, entry_layout)], tmp_path / "fallback.pdf"
    )
    with fitz.open(tmp_path / "fallback.pdf") as doc:
        assert text in doc[0].get_text()
        assert "Fixed Pay" not in doc[0].get_text()


def test_supported_text_has_no_fallback_markup():
    fonts = FontRegistry(Path("fonts").resolve())
    primary = fonts.resolve("ur", "native")
    text = "یہ ایک مثال ہے 123"
    assert fonts.glyph_fallbacks(text, primary) == {}
    content, faces = fallback_markup(text, primary, False, fonts)
    assert content == text and faces == ""


def test_truly_missing_glyph_is_still_rejected():
    fonts = FontRegistry(Path("fonts").resolve())
    b = N(bbox=[30, 30, 360, 120], style={"size": 14, "color": 0, "fixed_box": True})
    t = N(
        text="مثال \U0010ffff", target_language="ur", script="native", metadata_json={}
    )
    assert (
        ReorganiserAgent(fonts).run(b, t, N(width=400, height=200), [])["fit_status"]
        == "unsupported_glyphs"
    )
