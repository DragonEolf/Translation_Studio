from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as N

import pymupdf as fitz
import pytest

from app.services.cell_layout import CellLayout
from app.services.text_fitter import FontRegistry, rich_markup


def entry(i, source_size, y, height, text, cell=(20, 20, 330, 150), bold=False):
    style = {
        "font": "Helvetica",
        "size": source_size,
        "bold": bold,
        "italic": False,
        "color": 0,
    }
    runs = [
        {"text": "● ", "style": {**style, "bold": False}},
        {"text": text, "style": style},
        {
            "text": "1",
            "style": {**style, "size": source_size * 0.5, "superscript": True},
        },
    ]
    b = N(
        id=str(i),
        bbox=[30, y, 320, y + height],
        style={
            "size": source_size,
            "table_cell_bbox": list(cell),
            "rich_text": {"version": 1},
            "color": 0,
        },
    )
    t = N(
        text="".join(r["text"] for r in runs),
        target_language="en",
        script="native",
        metadata_json={"rich_text": {"runs": runs}},
    )
    layout = N(
        bbox=list(b.bbox),
        font_size=source_size / 2 if i == 1 else source_size,
        font=None,
        fit_status="fitted",
    )
    return b, t, layout


@pytest.fixture
def fonts():
    return FontRegistry(Path("fonts").resolve())


def assert_fits(entries, fonts):
    for b, t, layout in entries:
        paths = {bold: fonts.resolve("en", "native", bold) for bold in (False, True)}
        content, css = rich_markup(
            t.metadata_json["rich_text"]["runs"],
            layout.font_size,
            b.style["size"],
            paths,
        )
        with fitz.open() as d:
            p = d.new_page(width=360, height=240)
            spare, _ = p.insert_htmlbox(
                fitz.Rect(layout.bbox), content, css=css, scale_low=1
            )
            assert spare >= 0


def test_cell_reflows_bullets_at_one_size_instead_of_minimum_of_old_boxes(fonts):
    es = [
        entry(0, 12, 25, 30, "A longer bullet with several words. " * 3),
        entry(1, 12, 62, 10, "A medium bullet with several words."),
        entry(2, 12, 85, 55, "Another long bullet with several words. " * 3, bold=True),
    ]
    originals = [deepcopy(b.bbox) for b, _, _ in es]
    styles = deepcopy([t.metadata_json for _, t, _ in es])
    changed = CellLayout(fonts).apply(
        es, [b for b, _, _ in es], N(width=360, height=240)
    )
    assert len(changed) == 3
    assert len({entry_layout.font_size for _, _, entry_layout in es}) == 1
    assert es[1][2].font_size > 6  # Does not inherit the middle box's old tiny fit.
    assert [b.bbox for b, _, _ in es] == originals
    assert [t.metadata_json for _, t, _ in es] == styles
    for i, (_, _, entry_layout) in enumerate(es):
        assert fitz.Rect((20, 20, 330, 150)).contains(fitz.Rect(entry_layout.bbox))
        for _, _, other in es[i + 1 :]:
            assert (
                fitz.Rect(entry_layout.bbox) & fitz.Rect(other.bbox)
            ).get_area() == 0
    assert_fits(es, fonts)


def test_different_original_sizes_keep_their_ratio(fonts):
    es = [
        entry(0, 16, 25, 25, "Heading", bold=True),
        entry(1, 10, 60, 20, "Body text with many more words. " * 6),
    ]
    CellLayout(fonts).apply(es, [b for b, _, _ in es], N(width=360, height=240))
    assert es[0][2].font_size / es[1][2].font_size == pytest.approx(1.6)
    assert es[0][2].font_size <= 16 and es[1][2].font_size <= 10
    assert_fits(es, fonts)


def test_missing_translation_and_images_are_barriers(fonts):
    a = entry(0, 12, 25, 25, "First bullet")
    missing = entry(1, 12, 60, 15, "Not translated")
    b = entry(2, 12, 90, 25, "Third bullet")
    es = [a, b]
    before = [deepcopy(vars(entry_layout)) for _, _, entry_layout in es]
    assert (
        CellLayout(fonts).apply(es, [a[0], missing[0], b[0]], N(width=360, height=240))
        == []
    )
    assert [vars(entry_layout) for _, _, entry_layout in es] == before
    es = [a, missing, b]
    before = [deepcopy(vars(entry_layout)) for _, _, entry_layout in es]
    assert (
        CellLayout(fonts).apply(
            es, [x[0] for x in es], N(width=360, height=240), images=[[60, 40, 120, 70]]
        )
        == []
    )
    assert [vars(entry_layout) for _, _, entry_layout in es] == before


def test_different_cells_and_non_rich_text_do_not_share_layout(fonts):
    a = entry(0, 12, 25, 25, "First bullet", cell=(20, 20, 330, 60))
    b = entry(1, 12, 80, 25, "Other cell", cell=(20, 70, 330, 150))
    before = [deepcopy(vars(entry_layout)) for _, _, entry_layout in [a, b]]
    assert CellLayout(fonts).apply([a, b], [a[0], b[0]], N(width=360, height=240)) == []
    assert [vars(entry_layout) for _, _, entry_layout in [a, b]] == before
    a[0].style.pop("rich_text")
    b[0].style.pop("rich_text")
    assert CellLayout(fonts).apply([a, b], [a[0], b[0]], N(width=360, height=240)) == []


def test_api_pipeline_applies_shared_cell_size(tmp_path, fonts):
    from fastapi.testclient import TestClient
    from test_studio import wait

    from app.config import Settings
    from app.main import create_app
    from app.models import LayoutResult

    class Echo:
        async def translate(self, text, *args):
            return {
                "translation": text.replace(
                    "Hello", "Welcome with several additional words"
                ),
                "truncated": False,
            }

    with fitz.open() as d:
        p = d.new_page(width=420, height=240)
        for y in (25, 55, 205):
            p.draw_line((20, y), (400, y))
        for x in (20, 110, 400):
            p.draw_line((x, 25), (x, 205))
        p.insert_text((30, 45), "Label", fontsize=12)
        p.insert_text((120, 45), "Description", fontsize=12)
        p.insert_text((30, 90), "Project", fontsize=14)
        for y, text in [
            (73, "- Hello first bullet"),
            (89, "continuing on another line."),
            (117, "- Hello middle bullet"),
            (151, "- Hello last bullet"),
            (167, "with a second line."),
        ]:
            p.insert_text((120, y), text, fontsize=12)
        data = d.tobytes()
    app = create_app(
        Settings(studio_data_dir=tmp_path, studio_font_dir=Path("fonts").resolve()),
        Echo(),
    )
    with TestClient(app) as c:
        uploaded = c.post("/v1/documents", files={"file": ("cell.pdf", data)})
        did = uploaded.json()["document_id"]
        wait(c, uploaded)
        job = wait(
            c,
            c.post(
                f"/v1/documents/{did}/translations",
                json={"target_language": "en", "layout_mode": "paragraphs"},
            ),
        )
        assert job["status"] == "completed", job
        body = [
            b
            for b in c.get(f"/v1/documents/{did}/blocks").json()
            if b["source_text"].startswith("- Hello")
        ]
        assert len(body) == 3
        with app.state.sessions() as session:
            sizes = [session.get(LayoutResult, b["id"]).font_size for b in body]
        assert max(sizes) - min(sizes) < 0.0001
