"""Offline contract tests. No provider calls or user documents are used."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pymupdf
import pytest

from benchmarks.ugc.run import DEFAULT_RUBRIC, JUDGE, evaluate, pipeline, plan, prepare


def document(path, count):
    with pymupdf.open() as doc:
        for page in range(count):
            doc.new_page().insert_text((40, 50), f"Page {page+1}")
        doc.save(path)
    return path


def test_plan_uses_only_same_numbered_reference_page(tmp_path):
    en = document(tmp_path / "en.pdf", 50)
    hi = document(tmp_path / "hi.pdf", 51)
    cases = plan(en, hi, 1, 50)
    assert len(cases) == 50
    assert cases[0]["reference_pages"] == [1]
    assert cases[-1]["reference_pages"] == [50]
    with pytest.raises(ValueError, match="exceeds"):
        plan(en, hi, 2, 50)


def test_preparation_preserves_source_pages_and_checks_integrity(tmp_path):
    en = document(tmp_path / "en.pdf", 3)
    hi = document(tmp_path / "hi.pdf", 4)
    cases = plan(en, hi, 2, 1)
    out = tmp_path / "suite"
    prepare(en, hi, out, cases, {"version": 1})
    with pymupdf.open(out / cases[0]["id"] / "source.pdf") as pdf:
        assert len(pdf) == 1
        assert "Page 2" in pdf[0].get_text()
    prepare(en, hi, out, cases, {"version": 1})
    (out / cases[0]["id"] / "source.pdf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        prepare(en, hi, out, cases, {"version": 1})


def test_pipeline_overlaps_judging_with_next_translation(tmp_path):
    cases = [dict(id=f"page-{i:04d}") for i in range(1, 4)]
    for case in cases:
        (tmp_path / case["id"]).mkdir()
    first_judge_started = threading.Event()
    second_translation_started = threading.Event()
    judged = []

    def translate(case, directory):
        if case == cases[1]:
            assert first_judge_started.wait(3)
            second_translation_started.set()

    def judge(case, directory):
        if case == cases[0]:
            first_judge_started.set()
            assert second_translation_started.wait(3)
        judged.append(case["id"])
        return dict(score=80, criteria=[], issues=[])

    # Fake judges operate only on Events, never PyMuPDF; production uses processes.
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pipeline(cases, tmp_path, translate, lambda c, d: pool.submit(judge, c, d)) == 0
    assert judged == [c["id"] for c in cases]
    result = json.loads((tmp_path / "results.json").read_text())
    assert result["completed"] == 3
    assert result["mean_score"] == 80


def test_translation_failure_continues_and_requires_explicit_retry(tmp_path):
    cases = [dict(id="first"), dict(id="second")]
    for case in cases:
        (tmp_path / case["id"]).mkdir()
    attempts = []

    def translate(case, directory):
        attempts.append(case["id"])
        if case["id"] == "first":
            raise RuntimeError("Translation failed")

    with ThreadPoolExecutor(max_workers=1) as pool:
        submit = lambda c, d: pool.submit(lambda: dict(score=90, criteria=[], issues=[]))
        assert pipeline(cases, tmp_path, translate, submit) == 1
        assert pipeline(cases, tmp_path, translate, submit) == 1
    assert attempts.count("first") == 1
    result = json.loads((tmp_path / "results.json").read_text())
    assert result["failed"] == 1
    assert result["completed"] == 1
    assert result["cases"][0]["failed_stage"] == "translation"


def test_similarity_evaluator_sends_only_two_hindi_documents(tmp_path, monkeypatch):
    # Deliberately omit source.pdf: the evaluator must neither read nor send it.
    document(tmp_path / "reference.pdf", 1)
    document(tmp_path / "candidate.pdf", 1)
    calls = []

    class FakeRouter:
        def __init__(self, *args, **kwargs):
            pass

        def complete(self, model, prompt, content, schema, max_tokens):
            calls.append((prompt, content))
            rubric = json.loads(DEFAULT_RUBRIC.read_text())
            answer = dict(criteria=[dict(criterion=c["id"], points=c["max_points"])
                                    for c in rubric["criteria"]], issues=[])
            return json.dumps(answer), {"model": "fake", "usage": {}}

    monkeypatch.setattr("benchmarks.ugc.run.OpenRouter", FakeRouter)
    case = dict(id="page-0001", source_page=1, reference_pages=[1])
    result = evaluate(case, tmp_path, "fake-key", "fake", DEFAULT_RUBRIC.read_text(), 30)
    assert result["score"] == 100
    prompt, content = calls[0]
    labels = [json.loads(item["text"])["document"] for item in content
              if item["type"] == "text" and "document" in json.loads(item["text"])]
    assert labels == ["REFERENCE", "CANDIDATE"]
    assert sum(item["type"] == "image_url" for item in content) == 2
    assert "reference_context" not in result
    assert prompt == JUDGE
    # Cached evaluation also requires no English source file or extra model call.
    evaluate(case, tmp_path, "fake-key", "fake", DEFAULT_RUBRIC.read_text(), 30)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,category", [(429, "rate_limit"), (503, "provider_server_error"), (401, "authentication"), (400, "request_rejected")])
async def test_request_diagnostics_preserve_http_category_without_secrets(tmp_path, status, category):
    import httpx
    from benchmarks.ugc.diagnostics import RecordingTransport, request_summary
    transport = RecordingTransport(tmp_path / "bodhan-requests.jsonl", "bodhan",
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json={"secret": "private body"})))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post("https://example.test", headers={"Authorization": "Bearer secret-key"}, json={"text": "private document"})
    assert response.status_code == status
    text = (tmp_path / "bodhan-requests.jsonl").read_text()
    assert "private" not in text and "secret" not in text
    summary = request_summary(tmp_path)["bodhan"]
    assert summary["categories"] == {category: 1}
    assert summary["http_statuses"] == {str(status): 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("exception,category", [("ReadTimeout", "timeout"), ("ConnectError", "connection_error")])
async def test_request_diagnostics_distinguish_transport_errors(tmp_path, exception, category):
    import httpx
    from benchmarks.ugc.diagnostics import RecordingTransport
    def fail(request):
        raise getattr(httpx, exception)("sensitive upstream detail", request=request)
    transport = RecordingTransport(tmp_path / "bodhan-requests.jsonl", "bodhan", transport=httpx.MockTransport(fail))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(getattr(httpx, exception)):
            await client.get("https://example.test")
    text = (tmp_path / "bodhan-requests.jsonl").read_text()
    assert "sensitive" not in text
    event = json.loads(text)
    assert event["category"] == category
    assert event["exception_type"] == exception


@pytest.mark.asyncio
async def test_rate_limit_is_recorded_even_when_retry_succeeds(tmp_path):
    import httpx
    from app.providers.bodhan import BodhanProvider
    from benchmarks.ugc.diagnostics import RecordingTransport, request_summary
    calls = []
    def respond(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hindi"}, "finish_reason": "stop"}]})
    transport = RecordingTransport(tmp_path / "bodhan-requests.jsonl", "bodhan", transport=httpx.MockTransport(respond))
    result = await BodhanProvider("fake", transport=transport).translate("Hello", "hi", "native")
    assert result["translation"] == "Hindi"
    assert request_summary(tmp_path)["bodhan"]["categories"] == {"rate_limit": 1, "success": 1}


def test_terminal_failure_uses_http_evidence_and_separates_validation(tmp_path):
    from benchmarks.openrouter.run import JobFailure
    from benchmarks.ugc.diagnostics import failure_details
    event = dict(at="2026-10-29T13:00:00Z", service="bodhan", category="rate_limit", http_status=429)
    (tmp_path / "bodhan-requests.jsonl").write_text(json.dumps(event)+"\n")
    assert failure_details(JobFailure(dict(status="failed", error_code="provider_unavailable")), tmp_path, "translation")["error_category"] == "rate_limit"
    assert failure_details(JobFailure(dict(status="failed", error_code="invalid_provider_response")), tmp_path, "translation")["error_category"] == "invalid_response"
    assert failure_details(ZeroDivisionError(), tmp_path, "translation")["error_category"] == "internal_error"
