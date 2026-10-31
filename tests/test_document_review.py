"""Review snapshots must reflect visible geometry without mutating findings."""

import pymupdf as fitz
from fastapi.testclient import TestClient
from test_studio import FakeProvider, pdf_bytes, wait

from app.config import Settings
from app.main import create_app
from app.models import Block, Document, Issue, Job, LayoutResult, Page, Translation


def seed(app, root):
    folder = root / "doc"
    folder.mkdir()
    with fitz.open() as pdf:
        page = pdf.new_page(width=300, height=400)
        page.set_rotation(90)
        pdf.new_page(width=300, height=400)
        pdf.save(folder / "source.pdf")
        pdf.save(folder / "final.pdf")
    for name in ["source-1.png", "page-1.png"]:
        (folder / name).write_bytes(b"preview")
    with app.state.sessions() as session:
        session.add(
            Document(
                id="doc",
                filename="example.pdf",
                sha256="hash",
                status="completed_with_issues",
                target_language="en",
                page_count=2,
            )
        )
        session.flush()
        session.add_all(
            [
                Page(
                    id=f"doc-p{n}",
                    document_id="doc",
                    number=n,
                    width=400 if n == 1 else 300,
                    height=300 if n == 1 else 400,
                )
                for n in [1, 2]
            ]
        )
        session.flush()
        session.add_all(
            [
                Block(
                    id=key,
                    document_id="doc",
                    page_number=number,
                    type="text",
                    reading_order=order,
                    bbox=[10, 20, 50, 40],
                    source_text="Original",
                    style=style,
                )
                for key, number, order, style in [
                    (
                        "translated",
                        1,
                        0,
                        {"source_regions": [[10, 20, 30, 30], [10, 30, 50, 40]]},
                    ),
                    ("retained", 1, 1, {}),
                    ("missing", 2, 0, {}),
                    ("retired", 1, 2, {"retired": True}),
                ]
            ]
        )
        session.flush()
        session.add_all(
            [
                Translation(
                    block_id=key,
                    target_language="en",
                    script="native",
                    text="Translation",
                    revision=1,
                )
                for key in ["translated", "retained"]
            ]
        )
        session.add_all(
            [
                LayoutResult(
                    block_id=key,
                    bbox=[10, 20, 60, 45],
                    font="sans-serif",
                    font_size=10,
                    fit_status=status,
                )
                for key, status in [("translated", "fitted"), ("retained", "overflow")]
            ]
        )
        session.add_all(
            [
                Issue(
                    document_id="doc",
                    block_id=block,
                    page_number=page,
                    code=code,
                    severity=severity,
                    stage="checker",
                    message=code,
                    resolution=resolution,
                )
                for block, page, code, severity, resolution in [
                    (
                        "translated",
                        1,
                        "small_text",
                        "warning",
                        "manual_review_required",
                    ),
                    ("translated", 1, "collision", "error", "resolved"),
                    ("retired", 1, "missing_text", "error", "manual_review_required"),
                    (None, 1, "graphics_changed", "error", "manual_review_required"),
                    (
                        None,
                        None,
                        "document_warning",
                        "warning",
                        "manual_review_required",
                    ),
                ]
            ]
        )
        session.commit()


def test_review_maps_rotated_source_and_output_and_keeps_page_findings(tmp_path):
    app = create_app(Settings(_env_file=None, studio_data_dir=tmp_path))
    with TestClient(app) as client:
        seed(app, tmp_path)
        response = client.get("/v1/documents/doc/review")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["pages"][0]["width"] == 400
        assert data["pages"][1]["translated_preview"] is None
        regions = {r["id"]: r for r in data["regions"]}
        assert "retired" not in regions
        translated = regions["translated"]
        assert translated["status"] == "review"
        assert translated["source_rects"] == [[370, 10, 380, 30], [360, 10, 370, 50]]
        assert translated["translated_rects"] == [[355, 10, 380, 60]]
        assert [i["code"] for i in translated["issues"]] == ["small_text"]
        assert regions["retained"]["status"] == "change"
        assert regions["retained"]["rendered"] is False
        assert (
            regions["retained"]["translated_rects"]
            == regions["retained"]["source_rects"]
        )
        assert regions["missing"]["status"] == "change"
        assert regions["missing"]["translated_text"] is None
        assert regions["page-1-findings"]["source_rects"] == []
        assert regions["page-1-findings"]["status"] == "change"
        assert regions["page-None-findings"]["page_number"] is None
        assert (
            client.get("/v1/documents/doc/review").json()["version"] == data["version"]
        )
        with app.state.sessions() as session:
            issue = session.get(
                Issue,
                session.query(Issue.id).filter(Issue.code == "small_text").scalar(),
            )
            assert issue.resolution == "manual_review_required"
            translation = (
                session.query(Translation)
                .filter(Translation.block_id == "translated")
                .one()
            )
            translation.text = "Corrected translation"
            session.commit()
        assert (
            client.get("/v1/documents/doc/review").json()["version"] != data["version"]
        )


def test_review_unavailable_busy_and_draft_output(tmp_path):
    app = create_app(Settings(_env_file=None, studio_data_dir=tmp_path))
    with TestClient(app) as client:
        assert client.get("/v1/documents/unknown/review").status_code == 404
        seed(app, tmp_path)
        (tmp_path / "doc/final.pdf").rename(tmp_path / "doc/draft.pdf")
        assert client.get("/v1/documents/doc/review").json()["output"] == "draft.pdf"
        with app.state.sessions() as session:
            job = Job(
                id="active", document_id="doc", operation="render", status="running"
            )
            session.add(job)
            session.commit()
        assert client.get("/v1/documents/doc/review").status_code == 409
        with app.state.sessions() as session:
            session.get(Job, "active").status = "cancelled"
            session.commit()
        (tmp_path / "doc/draft.pdf").unlink()
        assert (
            client.get("/v1/documents/doc/review").json()["error"]["code"]
            == "artifact_not_ready"
        )


def test_completed_pipeline_review_uses_real_previews_without_provider_calls(tmp_path):
    provider = FakeProvider()
    app = create_app(Settings(_env_file=None, studio_data_dir=tmp_path), provider)
    with TestClient(app) as client:
        upload = client.post(
            "/v1/documents",
            files={"file": ("sample.pdf", pdf_bytes(), "application/pdf")},
        )
        document_id = upload.json()["document_id"]
        assert wait(client, upload)["status"] == "completed"
        translated = wait(
            client,
            client.post(
                f"/v1/documents/{document_id}/translations",
                json={"target_language": "en"},
            ),
        )
        assert translated["status"] == "completed"
        calls = provider.calls
        data = client.get(f"/v1/documents/{document_id}/review").json()
        assert len(data["pages"]) == 2
        assert len(data["regions"]) == 2
        assert all(r["status"] == "clear" and r["rendered"] for r in data["regions"])
        assert all("Welcome" in r["translated_text"] for r in data["regions"])
        for page in data["pages"]:
            for key in ["source_preview", "translated_preview"]:
                preview = client.get(page[key])
                assert preview.status_code == 200
                assert preview.content.startswith(b"\x89PNG")
        assert provider.calls == calls
