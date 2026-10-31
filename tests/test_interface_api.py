"""Read-only UI metadata must support recovery without exposing credentials."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Document, Job


def test_health_reports_configuration_and_font_readiness_without_secrets(tmp_path):
    settings = Settings(
        _env_file=None,
        studio_data_dir=tmp_path,
        studio_font_dir=None,
        bodhan_api_key="private-translation-key",
        bodhan_ocr_api_key="private-ocr-key",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/health")
        health = response.json()
        assert health["translation_configured"] is True
        assert health["ocr_configured"] is True
        assert "en" in health["available_font_languages"]
        assert "ta" not in health["available_font_languages"]
        assert "private-" not in response.text


def test_latest_job_can_recover_a_translation_submission(tmp_path):
    settings = Settings(_env_file=None, studio_data_dir=tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        now = datetime.now(timezone.utc)
        with app.state.sessions() as session:
            session.add(Document(id="doc", filename="test.pdf", sha256="test"))
            session.flush()
            session.add_all(
                [
                    Job(
                        id="inspection",
                        document_id="doc",
                        operation="inspect",
                        status="completed",
                        created_at=now - timedelta(seconds=10),
                    ),
                    Job(
                        id="translation",
                        document_id="doc",
                        operation="translate",
                        status="completed_with_issues",
                        created_at=now,
                        payload={"internal_data": "not-for-ui-metadata"},
                    ),
                ]
            )
            session.commit()
        response = client.get("/v1/documents/doc")
        assert response.json()["latest_job"] == {
            "id": "translation",
            "operation": "translate",
            "status": "completed_with_issues",
        }
        assert "not-for-ui-metadata" not in response.text
        assert client.get("/v1/documents/missing").status_code == 404
