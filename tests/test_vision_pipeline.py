import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from test_studio import wait

from app.config import Settings, StudioError
from app.main import create_app
from benchmarks.legacy.vision import RepairProposal


@pytest.mark.parametrize("unavailable,expected_calls", [(False, 0), (True, 0)])
def test_pipeline_uses_saved_text_and_bounds_review_calls_per_document(
    tmp_path, unavailable, expected_calls
):
    class Expanded:
        async def translate(self, text, *args):
            return {
                "translation": text.replace("Hello", "Expanded content " * 100),
                "truncated": False,
            }

    class Reviewer:
        calls = 0

        async def solve(self, *args):
            pytest.fail("Reasoning repairs must never be called")

        async def revise(self, *args):
            pytest.fail("Reasoning revisions must never be called")

        def review(self, context, source, rendered):
            self.calls += 1
            if unavailable:
                raise StudioError("vision_review_unavailable", "Offline", 503)
            return RepairProposal(
                block_id=context["block_id"], actions=[{"type": "manual_review"}]
            ), {}

    reviewer = Reviewer()
    settings = Settings(
        _env_file=None,
        studio_data_dir=tmp_path / "data",
        studio_vision_max_regions=2,
        studio_typography_max_refits=0,
    )
    app = create_app(settings, Expanded(), reasoning=reviewer, vision_reviewer=reviewer)
    with fitz.open() as pdf:
        for _ in range(3):
            pdf.new_page(width=300, height=200).insert_text(
                (30, 60), "Hello world", fontsize=12
            )
        data = pdf.tobytes()
    with TestClient(app) as client:
        assert "vision_review_enabled" not in client.get("/v1/health").json()
        upload = client.post("/v1/documents", files={"file": ("synthetic.pdf", data)})
        did = upload.json()["document_id"]
        wait(client, upload)
        job = wait(
            client,
            client.post(
                f"/v1/documents/{did}/translations",
                json={"target_language": "en", "layout_mode": "auto"},
            ),
        )
        assert job["status"] == "completed_with_issues", job
        block = client.get(f"/v1/documents/{did}/blocks").json()[0]
        assert (
            client.post(
                f"/v1/blocks/{block['id']}/revise", json={"instruction": "repair"}
            ).status_code
            == 410
        )
        assert reviewer.calls == expected_calls
        report = client.get(f"/v1/documents/{did}/artifacts/layout-report.json").json()
        assert "vision" not in report
        assert "vision" not in job["payload"]["layout_review_stats"]
        assert client.get(f"/v1/documents/{did}/artifacts/final.pdf").status_code == 200
        assert "vision_review_unavailable" not in {
            i["code"] for i in client.get(f"/v1/documents/{did}/issues").json()
        }
