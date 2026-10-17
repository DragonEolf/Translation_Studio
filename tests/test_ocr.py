import base64
import json
import httpx
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from app.config import Settings, StudioError
from app.main import create_app
from app.providers.ocr import BodhanOCRProvider
from app.agents.ocr_reader import OCRReaderAgent
from test_studio import FakeProvider, wait


def response():
    return {"choices":[{"finish_reason":"stop","message":{"content":"Hello scan"}}],"blocks":[{"order":0,"label":"Paragraph","type":"Text","bbox_xyxy":[40,45,260,85],"conf":.95,"text":"Hello scan"}],"usage":{"total_tokens":30}}


class FakeOCR:
    def __init__(self):
        self.calls = 0
    async def extract(self,png):
        self.calls += 1
        assert png.startswith(b"\x89PNG")
        return response()


def mixed_pdf():
    with fitz.open() as raster:
        page = raster.new_page(width=300,height=200)
        page.insert_text((40,70),"Hello scan",fontsize=16)
        page.draw_rect(fitz.Rect(40,120,100,160),color=(1,0,0))
        png = page.get_pixmap().tobytes("png")
    with fitz.open() as pdf:
        digital = pdf.new_page(width=300,height=200)
        digital.insert_text((40,70),"Hello digital")
        scan = pdf.new_page(width=300,height=200)
        scan.insert_image(scan.rect,stream=png)
        return pdf.tobytes()


@pytest.mark.parametrize('cache_enabled',[False,True])
def test_mixed_pdf_ocr_translation(tmp_path,cache_enabled):
    ocr = FakeOCR()
    app = create_app(Settings(studio_data_dir=tmp_path,studio_ocr_dpi=72,studio_font_dir=None,studio_cache_enabled=cache_enabled),FakeProvider(),ocr_provider=ocr)
    with TestClient(app) as client:
        upload = client.post("/v1/documents",files={"file":("mixed.pdf",mixed_pdf())})
        doc = upload.json()["document_id"]
        assert wait(client,upload)["status"] == "completed_with_issues"
        assert ocr.calls == 1
        pages = client.get(f"/v1/documents/{doc}/pages").json()
        assert [p["metadata_json"]["extraction_method"] for p in pages] == ["digital","ocr"]
        blocks = client.get(f"/v1/documents/{doc}/blocks").json()
        assert len(blocks) == 2
        assert blocks[1]["style"]["ocr"] is True
        assert blocks[1]["bbox"] == [40,45,260,85]
        job = wait(client,client.post(f"/v1/documents/{doc}/translations",json={"target_language":"en"}))
        assert job["status"] == "completed_with_issues"
        errors = client.get(f"/v1/documents/{doc}/issues").json()
        assert not [i for i in errors if i["severity"]=="error" and i["resolution"]!="resolved"],errors
        with fitz.open(stream=client.get(f"/v1/documents/{doc}/artifacts/final.pdf").content,filetype="pdf") as pdf:
            assert "Welcome digital" in " ".join(pdf[0].get_text().split())
            assert "Welcome scan" in " ".join(pdf[1].get_text().split())
        # Only the explicit cache opt-in may reuse a previous OCR response.
        from app.models import Job
        with app.state.sessions() as session:
            job = Job(document_id=doc,operation="inspect")
            session.add(job)
            session.commit()
            identity = job.id
        for _ in range(100):
            import time
            result = client.get(f"/v1/jobs/{identity}").json()
            if result["status"] not in {"queued","running"}:
                break
            time.sleep(.05)
        assert result["status"] == "completed_with_issues"
        assert ocr.calls == (1 if cache_enabled else 2)
        assert bool(list((tmp_path/doc).glob('ocr-*.json'))) == cache_enabled


async def test_ocr_transport_and_retry():
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests)==1:
            return httpx.Response(429,headers={"retry-after":"0"})
        return httpx.Response(200,json=response())
    result = await BodhanOCRProvider("test",transport=httpx.MockTransport(handler)).extract(b"png")
    payload = json.loads(requests[-1].content)
    assert payload["model"] == "indic-ocr"
    assert payload["messages"][0]["content"][0]["image_url"]["url"] == "data:image/png;base64,"+base64.b64encode(b"png").decode()
    assert result["blocks"][0]["text"] == "Hello scan"


@pytest.mark.parametrize("status,code",[(401,"ocr_authentication_failed"),(400,"ocr_request_rejected"),(502,"ocr_unavailable")])
async def test_ocr_errors(status,code):
    provider = BodhanOCRProvider("test",attempts=1,transport=httpx.MockTransport(lambda _:httpx.Response(status)))
    with pytest.raises(StudioError) as error:
        await provider.extract(b"png")
    assert error.value.code == code


def test_ocr_coordinates_and_invalid_output():
    reader = OCRReaderAgent()
    blocks = reader.blocks("doc",1,{"width":150,"height":100},response(),300,200)
    assert blocks[0]["bbox"] == [20,22.5,130,42.5]
    invalid = response()
    invalid["blocks"][0]["bbox_xyxy"] = [0,0,5000,50]
    with pytest.raises(StudioError):
        reader.blocks("doc",1,{"width":150,"height":100},invalid,300,200)
    invalid["choices"][0]["finish_reason"] = "length"
    with pytest.raises(StudioError):
        reader.blocks("doc",1,{"width":150,"height":100},invalid,300,200)
