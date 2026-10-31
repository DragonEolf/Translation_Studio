import pymupdf as fitz
from fastapi.testclient import TestClient
from test_studio import wait

from app.agents.block_maker import BlockMakerAgent
from app.agents.text_reader import TextReaderAgent
from app.config import Settings
from app.main import create_app
from app.services.rich_text import AUTO_PARAGRAPH_MIN_SPANS, group_blocks


def fixture(path):
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=250)
        for y, words in [
            (60, ["One ", "two ", "three ", "four ", "five."]),
            (130, ["Simple ", "bold ", "text."]),
        ]:
            x = 30
            for i, text in enumerate(words):
                font = "hebo" if i % 2 else "helv"
                page.insert_text((x, y), text, fontname=font, fontsize=14)
                x += fitz.get_text_length(text, fontname=font, fontsize=14)
        doc.save(path)
    return path.read_bytes()


def test_auto_groups_only_fragmented_paragraphs_and_keeps_source_styles(tmp_path):
    path = tmp_path / "source.pdf"
    fixture(path)
    pages = TextReaderAgent().run(path)
    spans = BlockMakerAgent().run("doc", pages)
    chosen = group_blocks(spans, pages, min_spans=AUTO_PARAGRAPH_MIN_SPANS)
    rich = [b for b in chosen if b["style"].get("rich_text")]
    plain = [b for b in chosen if not b["style"].get("rich_text")]
    assert len(rich) == 1 and len(plain) == 3
    assert len(rich[0]["style"]["rich_text"]["source_spans"]) == 5
    expected = {b["id"] for b in spans}
    actual = {b["id"] for b in plain} | {
        s["id"] for b in rich for s in b["style"]["rich_text"]["source_spans"]
    }
    assert actual == expected


def test_auto_api_mode_switches_and_persists_geometry(tmp_path):
    class Echo:
        async def translate(self, text, *args):
            return {"translation": text, "truncated": False}

    app = create_app(
        Settings(
            _env_file=None,
            studio_data_dir=tmp_path / "data",
            studio_typography_enabled=False,
        ),
        Echo(),
    )
    with TestClient(app) as client:
        upload = client.post(
            "/v1/documents",
            files={"file": ("source.pdf", fixture(tmp_path / "source.pdf"))},
        )
        did = upload.json()["document_id"]
        wait(client, upload)
        for mode, expected in [
            ("auto", 4),
            ("paragraphs", 2),
            ("spans", 8),
            ("auto", 4),
        ]:
            job = wait(
                client,
                client.post(
                    f"/v1/documents/{did}/translations",
                    json={"target_language": "en", "layout_mode": mode},
                ),
            )
            assert job["status"] in {"completed", "completed_with_issues"}, job
            assert len(client.get(f"/v1/documents/{did}/blocks").json()) == expected
            assert (
                client.get(f"/v1/documents/{did}/pages").json()[0]["metadata_json"][
                    "layout_mode"
                ]
                == mode
            )
            assert (
                client.get(f"/v1/documents/{did}/artifacts/final.pdf").status_code
                == 200
            )
