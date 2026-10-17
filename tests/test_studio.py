import time
import httpx
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from app.config import Settings, StudioError
from app.main import create_app
from app.providers.bodhan import BodhanProvider, validate_target
from app.agents.translator import protect, restore


class FakeProvider:
    def __init__(self):
        self.calls = 0
    async def translate(self, text, language, script):
        self.calls += 1
        return {"translation": text.replace("Hello", "Welcome"), "truncated": False}


def pdf_bytes():
    with fitz.open() as pdf:
        for i in range(2):
            page = pdf.new_page()
            page.insert_text((50,70), f"Hello page {i+1}", fontsize=16)
            page.draw_rect(fitz.Rect(50,150,150,200),color=(1,0,0))
        return pdf.tobytes()


def wait(client, response):
    assert response.status_code == 202, response.text
    identity = response.json()["job_id"]
    for _ in range(200):
        job = client.get(f"/v1/jobs/{identity}").json()
        if job["status"] in {"completed", "completed_with_issues", "failed"}:
            return job
        time.sleep(.05)
    pytest.fail("Job did not finish")


def test_end_to_end(tmp_path):
    provider = FakeProvider()
    app = create_app(Settings(studio_data_dir=tmp_path,studio_cache_enabled=True), provider)
    with TestClient(app) as client:
        response = client.post("/v1/documents",files={"file":("input.pdf",pdf_bytes(),"application/pdf")})
        doc = response.json()["document_id"]
        assert wait(client,response)["status"] == "completed"
        translated = wait(client,client.post(f"/v1/documents/{doc}/translations",json={"target_language":"en"}))
        assert translated["status"] == "completed", translated
        assert provider.calls == 2
        artifact = client.get(f"/v1/documents/{doc}/artifacts/final.pdf")
        with fitz.open(stream=artifact.content,filetype="pdf") as pdf:
            assert "Welcome" in pdf[0].get_text()
            assert "Hello" not in pdf[0].get_text()
            assert len(pdf[0].get_drawings()) == 1
        untouched = (tmp_path/doc/"page-2.pdf").stat().st_mtime_ns
        block = client.get(f"/v1/documents/{doc}/blocks").json()[0]
        job = wait(client,client.put(f"/v1/blocks/{block['id']}/translation",json={"text":"Edited page 1"}))
        assert job["status"] == "completed", job
        assert (tmp_path/doc/"page-2.pdf").stat().st_mtime_ns == untouched
        assert client.get(f"/v1/documents/{doc}/blocks").json()[0]["translation"]["revision"] == 2
        assert client.post(f"/v1/blocks/{block['id']}/revise",json={"instruction":"Improve it"}).json()["error"]["code"] == "reasoning_provider_not_configured"
        assert wait(client,client.post(f"/v1/documents/{doc}/translations",json={"target_language":"en"}))["status"] == "completed"
        assert provider.calls == 2


def test_invalid_upload(tmp_path):
    with TestClient(create_app(Settings(studio_data_dir=tmp_path),FakeProvider())) as client:
        assert client.post("/v1/documents",files={"file":("bad.pdf",b"bad")}).json()["error"]["code"] == "invalid_pdf"
        with fitz.open() as pdf:
            pdf.new_page()
            data = pdf.tobytes()
        result = wait(client,client.post("/v1/documents",files={"file":("scan.pdf",data)}))
        assert result["status"] == "completed"


def test_protected_literals():
    original = "See https://example.com and a@example.org at 12:30 with ID123"
    text, values = protect(original)
    assert restore(text,values) == original
    with pytest.raises(StudioError):
        restore("missing",values)


@pytest.mark.parametrize("language,script",[("xx","native"),("en","roman"),("sd-Arab","codemix")])
def test_target_validation(language,script):
    with pytest.raises(StudioError):
        validate_target(language,script)


async def test_bodhan_retry_and_truncation():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429,headers={"retry-after":"0"})
        return httpx.Response(200,json={"choices":[{"message":{"content":"hello"},"finish_reason":"length"}],"usage":{"total_tokens":4}})
    result = await BodhanProvider("secret",transport=httpx.MockTransport(handler)).translate("hello","en","native")
    assert result["truncated"]
    assert len(calls) == 2
    assert calls[0].url.path == "/v1/chat/completions"
    import json
    assert json.loads(calls[0].content) == {"model":"indic-translate","messages":[{"role":"user","content":"hello"}],"target_language_code":"en","target_script":"native"}
    assert result["translation"] == "hello"
    assert result["usage"] == {"total_tokens":4}


@pytest.mark.parametrize("body",[{}, {"choices":[]}, {"choices":[{"message":{"content":""},"finish_reason":"stop"}]}, {"choices":[{"message":{"content":"blocked"},"finish_reason":"content_filter"}]}])
async def test_invalid_chat_responses(body):
    provider = BodhanProvider("secret",transport=httpx.MockTransport(lambda _:httpx.Response(200,json=body)))
    with pytest.raises(StudioError) as error:
        await provider.translate("hello","hi","native")
    assert error.value.code == "invalid_provider_response"


async def test_successful_chat_response():
    body = {"choices":[{"message":{"content":"नमस्ते"},"finish_reason":"stop"}]}
    provider = BodhanProvider("secret",transport=httpx.MockTransport(lambda _:httpx.Response(200,json=body)))
    result = await provider.translate("Hello","hi","native")
    assert result["translation"] == "नमस्ते"
    assert result["truncated"] is False


@pytest.mark.parametrize("status,code",[(401,"invalid_api_key"),(400,"provider_request_rejected"),(500,"provider_unavailable")])
async def test_provider_errors(status,code):
    provider = BodhanProvider("secret",attempts=1,transport=httpx.MockTransport(lambda _:httpx.Response(status)))
    with pytest.raises(StudioError) as error:
        await provider.translate("hello","en","native")
    assert error.value.code == code


def test_truncation_and_missing_fonts_keep_source(tmp_path):
    class Truncated:
        async def translate(self,*args):
            return {"translation":"partial","truncated":True}
    with TestClient(create_app(Settings(studio_data_dir=tmp_path),Truncated())) as client:
        response = client.post("/v1/documents",files={"file":("input.pdf",pdf_bytes())})
        doc = response.json()["document_id"]
        wait(client,response)
        job = wait(client,client.post(f"/v1/documents/{doc}/translations",json={"target_language":"hi"}))
        assert job["status"] == "failed"
        assert job["error_code"] == "reconstruction_failed"
        assert "translation_truncated" in {i["code"] for i in client.get(f"/v1/documents/{doc}/issues").json()}
        assert client.get(f"/v1/documents/{doc}/artifacts/final.pdf").status_code==404
        with fitz.open(stream=client.get(f"/v1/documents/{doc}/artifacts/draft.pdf").content,filetype="pdf") as pdf:
            assert "Hello" in pdf[0].get_text()


def test_fixed_box_shrinking_and_font_issue(tmp_path):
    provider = FakeProvider()
    with TestClient(create_app(Settings(studio_data_dir=tmp_path,studio_font_dir=None),provider)) as client:
        response = client.post("/v1/documents",files={"file":("input.pdf",pdf_bytes())})
        doc = response.json()["document_id"]
        wait(client,response)
        failed=wait(client,client.post(f"/v1/documents/{doc}/translations",json={"target_language":"hi"}))
        assert failed["status"] == "failed"
        assert failed["error_code"] == "reconstruction_failed"
        assert "unsupported_glyphs" in {i["code"] for i in client.get(f"/v1/documents/{doc}/issues").json()}
        wait(client,client.post(f"/v1/documents/{doc}/translations",json={"target_language":"en"}))
        block = client.get(f"/v1/documents/{doc}/blocks").json()[0]
        job = wait(client,client.put(f"/v1/blocks/{block['id']}/translation",json={"text":"Long content " * 500 + "1"}))
        assert job["status"] == "completed"
        with client.app.state.sessions() as session:
            from app.models import LayoutResult
            layout = session.get(LayoutResult,block["id"])
            assert layout.bbox == block["bbox"]
            assert layout.fit_status == "fitted"
            assert 0 < layout.font_size < 1


def test_failed_job_resumes_blocks(tmp_path):
    class Intermittent(FakeProvider):
        async def translate(self,*args):
            if self.calls == 1:
                self.calls += 1
                raise StudioError("provider_unavailable","Temporary failure")
            return await super().translate(*args)
    provider = Intermittent()
    with TestClient(create_app(Settings(studio_data_dir=tmp_path,studio_cache_enabled=True),provider)) as client:
        response = client.post("/v1/documents",files={"file":("input.pdf",pdf_bytes())})
        doc = response.json()["document_id"]
        wait(client,response)
        assert wait(client,client.post(f"/v1/documents/{doc}/translations",json={"target_language":"en"}))["status"] == "failed"
        assert wait(client,client.post(f"/v1/documents/{doc}/translations",json={"target_language":"en"}))["status"] == "completed"
        assert provider.calls == 3


def test_encrypted_and_oversized(tmp_path):
    with TestClient(create_app(Settings(studio_data_dir=tmp_path,studio_max_upload_mb=1),FakeProvider())) as client:
        assert client.post("/v1/documents",files={"file":("huge.pdf",b"%PDF-"+b"a"*1024*1024)}).status_code == 413
        with fitz.open(stream=pdf_bytes(),filetype="pdf") as pdf:
            data = pdf.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw="owner",user_pw="user")
        job = wait(client,client.post("/v1/documents",files={"file":("encrypted.pdf",data)}))
        assert job["error_code"] == "encrypted_pdf"


def test_worker_recovers_running_job(tmp_path):
    from app.models import Document, Job
    app = create_app(Settings(studio_data_dir=tmp_path),FakeProvider())
    folder = tmp_path/"restart"
    folder.mkdir()
    (folder/"source.pdf").write_bytes(pdf_bytes())
    with app.state.sessions() as session:
        session.add(Document(id="restart",filename="input.pdf",sha256="test"))
        session.flush()
        session.add(Job(id="restart-job",document_id="restart",operation="inspect",status="running"))
        session.commit()
    with TestClient(app) as client:
        for _ in range(100):
            job = client.get("/v1/jobs/restart-job").json()
            if job["status"] == "completed":
                break
            time.sleep(.05)
        assert job["status"] == "completed"


def test_simple_table_extraction(tmp_path):
    from app.agents.text_reader import TextReaderAgent
    from app.agents.block_maker import BlockMakerAgent
    path = tmp_path/"table.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        for y in [60,100,140]:
            page.draw_line((50,y),(350,y))
        for x in [50,200,350]:
            page.draw_line((x,60),(x,140))
        for x,y,text in [(60,85,"Name"),(210,85,"Count"),(60,125,"Items"),(210,125,"12")]:
            page.insert_text((x,y),text)
        pdf.save(path)
    blocks = BlockMakerAgent().run("table",TextReaderAgent().run(path))
    assert len(blocks) == 4
    assert all(b["type"] == "table_cell" for b in blocks)
