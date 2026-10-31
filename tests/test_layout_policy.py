"""Document-family regressions for source reconstruction and safe anchoring."""

from collections import Counter
from types import SimpleNamespace as N

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from test_studio import wait

from app.agents.block_maker import BlockMakerAgent
from app.agents.text_reader import TextReaderAgent
from app.config import Settings
from app.main import create_app
from app.services.rich_text import group_blocks, source_runs

LONG = "We develop practical biological methods for reliable research in many settings"
NEXT = "and connect these methods with carefully validated computational tools"
TAIL = "that people can actually use."


def extract(path):
    pages = TextReaderAgent().run(path)
    raw = BlockMakerAgent().run("fixture", pages)
    return pages, raw, group_blocks(raw, pages, min_spans=4)


def assert_coverage(raw, grouped):
    identities = [
        s["id"]
        for b in grouped
        for s in b["style"].get("rich_text", {}).get("source_spans", [{"id": b["id"]}])
    ]
    assert Counter(identities) == Counter(b["id"] for b in raw)
    assert len(identities) == len(set(identities))


def prose_pdf(path, barrier=None):
    with fitz.open() as doc:
        p = doc.new_page(width=560, height=360)
        for y, text in [(50, LONG), (70, NEXT), (90, TAIL), (125, LONG), (145, TAIL)]:
            p.insert_text((30, y), text, fontsize=11)
        if barrier == "rule":
            p.draw_line((25, 57), (530, 57))
        elif barrier == "image":
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10))
            pix.clear_with(100)
            p.insert_image((200, 55, 230, 57), pixmap=pix)
        doc.save(path)
    return path.read_bytes()


def test_cross_block_source_paragraphs_join_without_losing_short_tail(tmp_path):
    path = tmp_path / "prose.pdf"
    prose_pdf(path)
    pages, raw, grouped = extract(path)
    assert len(raw) == 5
    assert len(grouped) == 2
    assert [b["style"]["layout_policy"]["line_count"] for b in grouped] == [3, 2]
    assert grouped[0]["source_text"] == f"{LONG} {NEXT} {TAIL}"
    assert all(b["style"]["layout_policy"]["decision"] == "reflow" for b in grouped)
    assert grouped[0]["bbox"][3] < grouped[1]["bbox"][1]
    assert_coverage(raw, grouped)


@pytest.mark.parametrize("barrier", ["rule", "image"])
def test_flow_never_crosses_visible_barriers(tmp_path, barrier):
    path = tmp_path / "barrier.pdf"
    prose_pdf(path, barrier)
    _, raw, grouped = extract(path)
    first = next(b for b in grouped if b["bbox"][1] < 45)
    assert first["source_text"] == LONG
    assert_coverage(raw, grouped)


def test_columns_keep_their_own_paragraphs(tmp_path):
    path = tmp_path / "columns.pdf"
    with fitz.open() as doc:
        p = doc.new_page(width=800, height=300)
        for x in (30, 430):
            for y, text in [
                (50, "We develop methods for carefully tested research"),
                (70, "and use these methods in practice."),
            ]:
                p.insert_text((x, y), text, fontsize=11)
        doc.save(path)
    _, raw, grouped = extract(path)
    assert len(grouped) == 2
    assert all(len(b["style"]["rich_text"]["source_spans"]) == 2 for b in grouped)
    assert all(b["bbox"][2] < 400 or b["bbox"][0] > 400 for b in grouped)
    assert_coverage(raw, grouped)


def test_resume_headings_dates_contacts_entries_and_bullets_remain_distinct(tmp_path):
    path = tmp_path / "resume.pdf"
    with fitz.open() as doc:
        p = doc.new_page(width=600, height=600)
        for x, y, text, font, size in [
            (30, 35, "EXPERIENCE", "hebo", 14),
            (30, 65, "Research Intern", "hebo", 11),
            (430, 65, "2024 - 2026", "helv", 11),
            (30, 85, "Institute of Biological Research", "helv", 11),
            (30, 115, "- " + LONG, "helv", 11),
            (30, 135, "- " + NEXT, "helv", 11),
            (30, 170, "SKILLS", "hebo", 14),
            (30, 200, "Python | SQL | Biology", "helv", 11),
            (30, 230, "email@example.com | +91 1234567890", "helv", 11),
            (30, 270, "Education:", "helv", 11),
            (30, 290, "BTech Biological Engineering", "helv", 11),
        ]:
            p.insert_text((x, y), text, fontname=font, fontsize=size)
        doc.save(path)
    _, raw, grouped = extract(path)
    assert len(grouped) == len(raw)
    assert {b["id"]: b["bbox"] for b in grouped} == {b["id"]: b["bbox"] for b in raw}
    assert_coverage(raw, grouped)


def test_form_fields_equations_and_small_captions_do_not_join(tmp_path):
    path = tmp_path / "form.pdf"
    with fitz.open() as doc:
        p = doc.new_page(width=600, height=300)
        for y, text in [
            (40, "Name:"),
            (60, "Krishna Karthikeyan"),
            (90, "Date:"),
            (110, "October 31, 2026"),
            (140, "E = m c^2"),
            (160, "x = y + z"),
            (200, "Figure 1. A sample result"),
        ]:
            p.insert_text((30, y), text, fontsize=11)
        doc.save(path)
    _, raw, grouped = extract(path)
    assert len(grouped) == len(raw)
    assert_coverage(raw, grouped)


def test_cross_block_inline_styles_and_numbers_keep_ownership(tmp_path):
    path = tmp_path / "styles.pdf"
    with fitz.open() as doc:
        p = doc.new_page(width=600, height=200)
        p.insert_text((30, 50), LONG, fontsize=11)
        p.insert_text((30, 70), "Results include ", fontsize=11)
        x = 30 + fitz.get_text_length("Results include ", fontsize=11)
        p.insert_text((x, 70), "1250 INR", fontsize=11, fontname="hebo")
        x += fitz.get_text_length("1250 INR", fontsize=11, fontname="hebo")
        p.insert_text((x, 70), " with stable performance.", fontsize=11)
        doc.save(path)
    _, raw, grouped = extract(path)
    assert len(grouped) == 1
    runs = source_runs(N(**grouped[0]))
    assert any(r["style"]["bold"] and "1250 INR" in r["text"] for r in runs)
    assert_coverage(raw, grouped)


@pytest.mark.parametrize("mode", ["auto", "paragraphs", "spans"])
def test_pipeline_modes_cover_every_span_and_send_whole_source_paragraph(
    tmp_path, mode
):
    class Echo:
        def __init__(self):
            self.requests = []

        async def translate(self, text, *args):
            self.requests.append(text)
            return {"translation": text, "truncated": False}

    provider = Echo()
    data = prose_pdf(tmp_path / "input.pdf")
    app = create_app(
        Settings(_env_file=None, studio_data_dir=tmp_path / "state"), provider
    )
    with TestClient(app) as c:
        uploaded = c.post("/v1/documents", files={"file": ("prose.pdf", data)})
        did = uploaded.json()["document_id"]
        wait(c, uploaded)
        job = wait(
            c,
            c.post(
                f"/v1/documents/{did}/translations",
                json={"target_language": "en", "layout_mode": mode},
            ),
        )
        assert job["status"] in {"completed", "completed_with_issues"}, job
        blocks = c.get(f"/v1/documents/{did}/blocks").json()
        assert len(blocks) == (5 if mode == "spans" else 2)
        if mode != "spans":
            assert any(
                f"{LONG} {NEXT} {TAIL}" in request for request in provider.requests
            )
        else:
            assert all(not b["style"].get("rich_text") for b in blocks)
        before = {b["id"]: b["translation"]["text"] for b in blocks}
        rerender = wait(c, c.post(f"/v1/documents/{did}/render"))
        assert rerender["status"] in {"completed", "completed_with_issues"}, rerender
        assert {
            b["id"]: b["translation"]["text"]
            for b in c.get(f"/v1/documents/{did}/blocks").json()
        } == before


def test_bullet_wraps_can_join_without_merging_the_next_item(tmp_path):
    path = tmp_path / "bullets.pdf"
    with fitz.open() as doc:
        p = doc.new_page(width=620, height=220)
        for y, text in [(45, "- " + LONG), (65, NEXT), (100, "- " + LONG), (120, TAIL)]:
            p.insert_text((30, y), text, fontsize=11)
        doc.save(path)
    _, raw, grouped = extract(path)
    assert len(grouped) == 2
    assert all(b["source_text"].count("- ") == 1 for b in grouped)
    assert all(b["style"]["layout_policy"]["role"] == "list" for b in grouped)
    assert_coverage(raw, grouped)


@pytest.mark.parametrize("missing", [False, True])
def test_render_only_keeps_saved_span_translations_until_fresh_translation(
    tmp_path, missing
):
    from sqlalchemy import select

    from app.models import Page, Translation

    class Echo:
        async def translate(self, text, *args):
            return {"translation": text, "truncated": False}

    app = create_app(
        Settings(_env_file=None, studio_data_dir=tmp_path / "state"), Echo()
    )
    with TestClient(app) as c:
        uploaded = c.post(
            "/v1/documents",
            files={"file": ("old.pdf", prose_pdf(tmp_path / "old.pdf"))},
        )
        did = uploaded.json()["document_id"]
        wait(c, uploaded)
        wait(
            c,
            c.post(
                f"/v1/documents/{did}/translations",
                json={"target_language": "en", "layout_mode": "spans"},
            ),
        )
        old = c.get(f"/v1/documents/{did}/blocks").json()
        with app.state.sessions() as session:
            page = session.get(Page, f"{did}-p1")
            page.metadata_json = {**page.metadata_json, "layout_mode": "auto"}
            if missing:
                t = session.scalar(
                    select(Translation).where(Translation.block_id == old[-1]["id"])
                )
                session.delete(t)
            session.commit()
        job = wait(c, c.post(f"/v1/documents/{did}/render"))
        assert job["status"] in {"completed", "completed_with_issues"}, job
        after = c.get(f"/v1/documents/{did}/blocks").json()
        assert {b["id"] for b in after} == {b["id"] for b in old}
        assert sum(b["translation"] is not None for b in after) == len(old) - int(
            missing
        )
        fresh = wait(
            c,
            c.post(
                f"/v1/documents/{did}/translations",
                json={"target_language": "en", "layout_mode": "auto"},
            ),
        )
        assert fresh["status"] in {"completed", "completed_with_issues"}, fresh
        assert len(c.get(f"/v1/documents/{did}/blocks").json()) == 2
