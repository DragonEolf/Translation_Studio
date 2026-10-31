"""Source-driven centering, including renderer/checker integration."""

from types import SimpleNamespace as N

import pymupdf as fitz
import pytest

from app.agents.checker import CheckerAgent
from app.services.renderer import Renderer
from app.services.table_alignment import (
    TableAlignment,
    source_centering,
    union,
    visible_lines,
)
from app.services.text_fitter import FontRegistry

CELL = [20, 20, 280, 180]


def source_page(doc, centered=True):
    page = doc.new_page(width=300, height=200)
    text = "Original label"
    x = 150 - fitz.get_text_length(text, fontsize=12) / 2 if centered else 30
    # Helvetica ascender/descender boxes, centered about y=100.
    font = fitz.Font("helv")
    y = 100 + (font.ascender + font.descender) * 6 if centered else 45
    page.insert_text((x, y), text, fontsize=12)
    page.draw_rect(CELL)
    return page


def entry(
    text="Translated label with additional words that wrap across multiple lines in this table cell",
    rich=False,
):
    style = dict(
        size=12,
        color=0,
        align="left",
        table_cell_bbox=CELL,
        source_regions=[[100, 90, 200, 110]],
    )
    runs = [{"text": text, "style": dict(size=12, color=0)}]
    if rich:
        style["rich_text"] = {"version": 1}
    block = N(
        id="label",
        source_text="Original label",
        bbox=[30, 30, 270, 70],
        style=style,
        confidence=1,
    )
    translation = N(
        text=text,
        target_language="en",
        script="native",
        metadata_json={"rich_text": {"runs": runs}} if rich else {},
    )
    layout = N(
        bbox=[30, 30, 270, 80], font="sans-serif", font_size=12, fit_status="fitted"
    )
    return block, translation, layout


@pytest.mark.parametrize("rich", [False, True])
def test_checker_detects_and_repair_centers_wrapped_content(rich):
    fonts = FontRegistry()
    service = TableAlignment(fonts)
    with fitz.open() as doc:
        page = source_page(doc)
        assert source_centering(page, CELL) == (True, True)
        item = entry(rich=rich)
        assert {w[1] for w in service.diagnostics(page, [item])} == {
            "table_horizontal_alignment",
            "table_vertical_alignment",
        }
        assert service.apply(page, [item], [item[0]]) == ["label"]
        assert service.diagnostics(page, [item]) == []
        lines = visible_lines(item, page.rect, fonts)
        assert len(lines) >= 2
        bounds = union(lines)
        assert (bounds.y0 + bounds.y1) / 2 == pytest.approx(100, abs=0.1)
        assert all((r.x0 + r.x1) / 2 == pytest.approx(150, abs=0.1) for r in lines)


def test_left_top_source_and_image_or_missing_translation_are_not_moved():
    service = TableAlignment(FontRegistry())
    with fitz.open() as doc:
        page = source_page(doc, centered=False)
        item = entry()
        assert service.apply(page, [item], [item[0]]) == []
        assert service.diagnostics(page, [item]) == []
    with fitz.open() as doc:
        page = source_page(doc)
        item = entry()
        assert service.apply(page, [item], [item[0]], images=[[40, 100, 60, 120]]) == []
        missing = N(
            id="missing", bbox=[30, 100, 200, 120], style={"table_cell_bbox": CELL}
        )
        assert service.apply(page, [item], [item[0], missing]) == []


def test_renderer_and_checker_use_centered_geometry(tmp_path):
    fonts = FontRegistry()
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    with fitz.open() as doc:
        page = source_page(doc)
        # Source region for redaction must match the actual source text.
        region = page.get_text("blocks")[0][:4]
        doc.save(source)
    item = entry("Translated label")
    item[0].style["source_regions"] = [list(region)]
    with fitz.open(source) as doc:
        TableAlignment(fonts).apply(doc[0], [item], [item[0]])
    Renderer(fonts).render_page(source, 1, [item], output)
    issues = CheckerAgent(fonts).run(source, output, 1, [item])
    assert not issues, issues
    with fitz.open(output) as doc:
        bounds = fitz.Rect(doc[0].get_text("blocks")[0][:4])
        assert (bounds.x0 + bounds.x1) / 2 == pytest.approx(150, abs=0.1)
        assert (bounds.y0 + bounds.y1) / 2 == pytest.approx(100, abs=0.1)


def test_multiple_paragraphs_center_as_one_group():
    service = TableAlignment(FontRegistry())
    with fitz.open() as doc:
        page = source_page(doc)
        first = entry("Heading")
        second = entry("A second paragraph")
        second[0].id = "body"
        first[2].bbox = [30, 30, 270, 50]
        second[2].bbox = [30, 60, 270, 80]
        entries = [first, second]
        assert service.apply(page, entries, [e[0] for e in entries]) == [
            "label",
            "body",
        ]
        assert service.diagnostics(page, entries) == []
        assert second[2].bbox[1] - first[2].bbox[1] == pytest.approx(30)


def test_indic_measurement_before_vector_outlining():
    from pathlib import Path

    fonts = FontRegistry(Path("fonts").resolve())
    if fonts.resolve("hi", "native") == "missing":
        pytest.skip("Devanagari font not installed")
    with fitz.open() as doc:
        page = source_page(doc)
        item = entry("प्रासंगिक कोर्सवर्क तकनीकी कौशल")
        item[1].target_language = "hi"
        item[2].font = fonts.resolve("hi", "native")
        service = TableAlignment(fonts)
        assert service.apply(page, [item], [item[0]]) == ["label"]
        assert service.diagnostics(page, [item]) == []
