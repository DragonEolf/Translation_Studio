import asyncio
from types import SimpleNamespace as N

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from app.agents.translator import TranslatorAgent, split_passage, source_hash
from app.agents.checker import normalized
from app.config import Settings, StudioError
from app.main import create_app
from test_ocr import mixed_pdf, response
from test_rich_text import rich_block
from test_studio import wait


class LimitedProvider:
    def __init__(self, limit=80):
        self.limit = limit
        self.calls = []

    async def translate(self, text, *args):
        self.calls.append(text)
        truncated = len(text) > self.limit
        return {"translation": "INCOMPLETE" if truncated else text.replace("Hello", "Welcome"),
                "truncated": truncated, "finish_reason": "length" if truncated else "stop",
                "usage": {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}}


async def test_truncated_paragraph_recovers_every_sentence_and_literal():
    source = "Hello first sentence costs 2023.\nHello second sentence uses API123.\nHello third sentence is complete."
    provider = LimitedProvider()
    text, metadata = await TranslatorAgent(provider).run(N(source_text=source), "en", "native")
    assert text == source.replace("Hello", "Welcome")
    assert metadata["source_hash"] == source_hash(source)
    assert metadata["protected_values"] == ["2023", "API123"]
    assert metadata["usage"]["total_tokens"] == 10 * len(provider.calls)
    diagnostics = metadata["translation_diagnostics"]
    assert diagnostics["splits"] > 0
    assert diagnostics["requests"][0]["finish_reason"] == "length"
    assert sum(r["truncated"] for r in diagnostics["requests"]) == diagnostics["splits"]
    assert "INCOMPLETE" not in text


@pytest.mark.parametrize("source", [
    "Read https://example.org/a?q=123 and email a@example.org for ID123 details.",
    "Check the formula $a + b = 123$ before choosing the next action.",
    "Check the formula $$a + b = 123$$ before choosing the next action.",
    r"Check the formula \(a + b = 123\) before choosing the next action.",
    "ꯑꯩꯈꯣꯏꯅ ꯂꯤꯁꯠ ꯑꯃꯗꯒꯤ ꯈꯟꯕꯒꯤꯗꯃꯛ ꯇ꯭ꯔꯦꯅꯤꯡ ꯄꯤꯔꯕ",
])
def test_split_retains_unicode_literals_math_and_order(source):
    left, separator, right = split_passage(source)
    assert left + separator + right == source
    for protected in ["https://example.org/a?q=123", "a@example.org", "ID123",
                      "$a + b = 123$", "$$a + b = 123$$", r"\(a + b = 123\)"]:
        if protected in source:
            assert protected in left or protected in right


@pytest.mark.parametrize("source", ["word", "$a + b$", "https://example.org/path"])
def test_unsplittable_content_is_not_cut(source):
    assert split_passage(source) is None


@pytest.mark.parametrize("limit,source", [(0, "Hello " * 200), (0, "unsplittable")])
async def test_unrecoverable_translation_is_bounded_and_retains_no_partial_text(limit, source):
    provider = LimitedProvider(limit)
    agent = TranslatorAgent(provider)
    results = [result async for result in agent.translate_batch([N(source_text=source)], "en", "native")]
    _, text, metadata, error = results[0]
    assert text is None and metadata is None
    assert error.code == "translation_truncated"
    assert len(provider.calls) <= agent.MAX_RECOVERY_CALLS
    assert len(error.diagnostics["requests"]) == len(provider.calls)
    assert error.diagnostics["usage"]["total_tokens"] == len(provider.calls) * 10
    assert "source retained" in error.message


async def test_total_request_budget_applies_even_when_some_branches_succeed():
    provider = LimitedProvider(20)
    agent = TranslatorAgent(provider)
    agent.MAX_RECOVERY_CALLS = 4
    with pytest.raises(StudioError) as exc:
        await agent.run(N(source_text="Hello world. " * 20), "en", "native")
    assert exc.value.code == "translation_truncated"
    assert len(provider.calls) == 4


@pytest.mark.parametrize("code", ["invalid_api_key", "provider_unavailable", "provider_request_rejected"])
async def test_provider_errors_do_not_trigger_splitting(code):
    class Failure:
        calls = 0
        async def translate(self, *args):
            self.calls += 1
            raise StudioError(code, "Unavailable")
    provider = Failure()
    with pytest.raises(StudioError) as exc:
        await TranslatorAgent(provider).run(N(source_text="A full sentence. Another sentence."), "en", "native")
    assert exc.value.code == code
    assert provider.calls == 1


async def test_recovery_preserves_rich_style_and_literals():
    block = rich_block()
    block.style["rich_text"]["runs"][0]["text"] = "Hello sentence. " * 10
    block.source_text = "".join(r["text"] for r in block.style["rich_text"]["runs"])
    provider = LimitedProvider()
    result = [r async for r in TranslatorAgent(provider).translate_batch([block], "en", "native")][0]
    _, text, metadata, error = result
    assert error is None
    assert text == block.source_text.replace("Hello", "Welcome")
    assert metadata["rich_text"]["fallback"]
    assert next(r["text"] for r in metadata["rich_text"]["runs"] if r["style"]["bold"]) == "1250.50 INR"
    assert metadata["usage"]["total_tokens"] == len(provider.calls)*10


async def test_recovery_state_is_isolated_for_concurrent_passages():
    provider = LimitedProvider(40)
    agent = TranslatorAgent(provider)
    sources = ["Hello one. " * 8, "Hello two."]
    results = await asyncio.gather(*(agent.run(N(source_text=s), "en", "native") for s in sources))
    for source, (text, metadata) in zip(sources, results):
        assert text.strip() == source.replace("Hello", "Welcome").strip()
        assert metadata["source_hash"] == source_hash(source)
    assert results[0][1]["translation_diagnostics"]["splits"] > 0
    assert results[1][1]["translation_diagnostics"]["splits"] == 0
    assert results[1][1]["usage"]["total_tokens"] == 10


@pytest.mark.parametrize("recover", [True, False])
def test_scanned_pdf_recovery_and_failure_diagnostics(tmp_path, recover):
    source = "Hello first sentence.\nHello second sentence.\nHello third sentence."
    class OCR:
        async def extract(self, png):
            body = response()
            body["blocks"][0].update(text=source, bbox_xyxy=[35, 30, 280, 110])
            return body
    settings = Settings(_env_file=None, studio_data_dir=tmp_path, studio_ocr_dpi=72,
                        studio_translation_batch_size=1)
    app = create_app(settings, LimitedProvider(40 if recover else 0), ocr_provider=OCR())
    with TestClient(app) as client:
        upload = client.post("/v1/documents", files={"file": ("scan.pdf", mixed_pdf())})
        document = upload.json()["document_id"]
        wait(client, upload)
        job = wait(client, client.post(f"/v1/documents/{document}/translations", json={"target_language": "en"}))
        if recover:
            assert job["status"] == "completed_with_issues", job
            assert job["payload"]["result"]["retained_source_blocks"] == 0
            assert job["payload"]["translation_batch_stats"]["recovered_passages"] == 1
            with fitz.open(stream=client.get(f"/v1/documents/{document}/artifacts/final.pdf").content, filetype="pdf") as pdf:
                text = " ".join(pdf[1].get_text().split())
                assert normalized(source.replace("Hello", "Welcome")) in normalized(text)
            issues = client.get(f"/v1/documents/{document}/issues").json()
            assert not any(i["code"] == "missing_translation" for i in issues)
            assert any(i["code"] == "passage_translation_split" for i in issues)
        else:
            assert job["status"] == "failed", job
            assert job["payload"]["result"]["rendered_blocks"] == 0
            failures = job["payload"]["translation_batch_stats"]["failed_passages"]
            assert failures
            assert all(d["requests"][0]["finish_reason"] == "length" for d in failures.values())
            assert client.get(f"/v1/documents/{document}/artifacts/final.pdf").status_code == 404
