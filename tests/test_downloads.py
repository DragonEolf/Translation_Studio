from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Document


@pytest.mark.parametrize(
    "original,language,artifact,expected",
    [
        ("Vehicle Pass copy.pdf", "hi", "final.pdf", "Vehicle Pass copy - Hindi.pdf"),
        ("Resume.PDF", "ta", "final.pdf", "Resume - Tamil.pdf"),
        ("My file.pdf", "ks-Deva", "final.pdf", "My file - Kashmiri (Devanagari).pdf"),
        ("தமிழ்.pdf", "en", "final.pdf", "தமிழ் - English.pdf"),
        ("Resume.pdf", "te", "draft.pdf", "Resume - Telugu - draft.pdf"),
        ("Resume.pdf", None, "draft.pdf", "Resume - draft.pdf"),
        ("bad\r\nname.pdf", "hi", "final.pdf", "bad__name - Hindi.pdf"),
    ],
)
def test_pdf_download_uses_original_filename_and_language(
    tmp_path, original, language, artifact, expected
):
    app = create_app(Settings(studio_data_dir=tmp_path, studio_font_dir=None))
    with TestClient(app) as client:
        with app.state.sessions() as session:
            session.add(
                Document(
                    id="doc", filename=original, sha256="test", target_language=language
                )
            )
            session.commit()
        folder = tmp_path / "doc"
        folder.mkdir(exist_ok=True)
        (folder / artifact).write_bytes(b"%PDF-test")
        response = client.get(f"/v1/documents/doc/artifacts/{artifact}")
        assert response.status_code == 200
        disposition = response.headers["content-disposition"]
        if "filename*=utf-8''" in disposition:
            actual = unquote(disposition.split("filename*=utf-8''", 1)[1])
        else:
            actual = disposition.split('filename="', 1)[1].rstrip('"')
        assert actual == expected
        assert response.content == b"%PDF-test"
