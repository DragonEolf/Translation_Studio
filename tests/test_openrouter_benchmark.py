import json
from pathlib import Path

import httpx
import pymupdf as fitz
import pytest

from benchmarks.openrouter.cases import DocumentHTML, plan
from benchmarks.openrouter.client import OpenRouter
from benchmarks.openrouter.compare import Rubric, compare
from benchmarks.openrouter.html_renderer import validate_html
from benchmarks.openrouter.render import font_paths
from benchmarks.openrouter.run import DEFAULT_RUBRIC, generate_case

FONTS = Path(__file__).resolve().parents[1] / "fonts" / "supported"


def make_pdfs(path):
    for name in ("source.pdf", "reference.pdf"):
        with fitz.open() as doc:
            doc.new_page().insert_text((40, 50), "Test document")
            doc.save(path / name)


DOCUMENTS = {
    "source_html": '<html><head><meta charset="utf-8"></head><body><h1>Test document</h1><p>English source content</p></body></html>',
    "reference_html": '<html><head><meta charset="utf-8"></head><body><h1>পরীক্ষামূলক নথি</h1><p>বাংলা অনুবাদ</p></body></html>',
}


class FakeRouter:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return json.dumps(self.response, ensure_ascii=False), {
            "model": "test",
            "usage": {"cost": 0},
        }


def test_balanced_languages_get_same_layouts():
    cases = plan(30, ["bn", "hi", "ta"])
    for language in ("bn", "hi", "ta"):
        assert len([c for c in cases if c["language"] == language]) == 10
        assert [c["theme"] for c in cases if c["language"] == language] == [
            c["theme"] for c in cases if c["language"] == "bn"
        ]
    assert all("layout" not in c for c in cases)
    with pytest.raises(ValueError, match="divisible"):
        plan(10, ["bn", "hi", "ta"])


def test_generation_resume_avoids_billable_calls(tmp_path, monkeypatch):
    case = plan(1, ["bn"])[0]
    router = FakeRouter(DOCUMENTS)
    executions = []

    def fake_execute(documents, directory, *args):
        executions.append(documents)
        make_pdfs(directory)

    monkeypatch.setattr("benchmarks.openrouter.generate.require_renderer", lambda: None)
    monkeypatch.setattr("benchmarks.openrouter.generate.render_pair", fake_execute)
    for _ in range(2):
        generate_case(router, case, tmp_path, font_paths(FONTS, "bn"), "test", 42)
    assert len(router.calls) == len(executions) == 1
    assert router.calls[0][1] == {"one_shot": True}
    assert "layout" not in json.loads(router.calls[0][0][2][0]["text"])
    assert (tmp_path / "source.pdf").exists() and (tmp_path / "reference.pdf").exists()


def test_failed_code_is_not_repaired_or_reexecuted(tmp_path, monkeypatch):
    router = FakeRouter(DOCUMENTS)
    executions = []

    def fail(*args):
        executions.append(1)
        raise RuntimeError("execution failed")

    monkeypatch.setattr("benchmarks.openrouter.generate.require_renderer", lambda: None)
    monkeypatch.setattr("benchmarks.openrouter.generate.render_pair", fail)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            generate_case(router, plan(1, ["bn"])[0], tmp_path, {}, "test", 42)
    assert len(router.calls) == len(executions) == 1
    saved = json.loads((tmp_path / "case.json").read_text())
    assert saved["status"] == "failed"
    assert saved["failed_stage"] == "rendering"


@pytest.mark.parametrize(
    "tag",
    [
        "<script>alert(1)</script>",
        '<iframe src="file:///etc/passwd"></iframe>',
        '<meta http-equiv="refresh" content="0;url=https://example.com">',
    ],
)
def test_active_document_content_is_rejected(tag):
    with pytest.raises(ValueError):
        validate_html("<html><body>" + tag + "</body></html>")


def assessment():
    return dict(
        criteria=[
            dict(criterion=c["id"], points=c["max_points"])
            for c in json.loads(DEFAULT_RUBRIC.read_text())["criteria"]
        ],
        issues=[
            dict(
                region_id="p1-text1",
                criterion="layout",
                improvement="Increase heading spacing.",
            )
        ],
    )


def run_comparison(tmp_path, response):
    make_pdfs(tmp_path)
    candidate = tmp_path / "candidate.pdf"
    candidate.write_bytes((tmp_path / "reference.pdf").read_bytes())
    router = FakeRouter(response)
    result = compare(
        router,
        tmp_path / "source.pdf",
        tmp_path / "reference.pdf",
        candidate,
        "bn",
        Rubric.model_validate_json(DEFAULT_RUBRIC.read_text()),
        "test",
        tmp_path,
    )
    return result, router


def test_comparer_sends_actual_images_and_sums_rubric(tmp_path):
    result, router = run_comparison(tmp_path, assessment())
    assert result["score"] == 100
    assert len([p for p in router.calls[0][0][2] if p["type"] == "image_url"]) == 3
    with fitz.open(tmp_path / "annotated.pdf") as pdf:
        assert len(list(pdf[0].annots())) == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["issues"][0].update(region_id="p2-text1"),
        lambda r: r["issues"][0].update(region_id="invented"),
        lambda r: r["issues"][0].update(criterion="unknown"),
        lambda r: r["issues"][0].update(improvement="one\ntwo"),
        lambda r: r["criteria"][0].update(points=50),
        lambda r: r["criteria"].pop(),
    ],
)
def test_comparer_rejects_invalid_regions_and_scores(tmp_path, change):
    response = assessment()
    change(response)
    with pytest.raises(ValueError):
        run_comparison(tmp_path, response)
    assert not (tmp_path / "evaluation.json").exists()


def test_client_uses_structured_output_and_does_not_leak_error_body():
    def handle(request):
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(401, json={"error": "private diagnostic"})

    router = OpenRouter("secret", transport=httpx.MockTransport(handle))
    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        router.complete("test", "system", "user", DocumentHTML.model_json_schema(), 100)
    assert "private diagnostic" not in str(error.value)


@pytest.mark.parametrize("status", [429, 500])
def test_one_shot_disables_http_retries_and_provider_fallbacks(status):
    requests = []

    def handle(request):
        requests.append(request)
        assert json.loads(request.content)["provider"]["allow_fallbacks"] is False
        return httpx.Response(status)

    router = OpenRouter("secret", transport=httpx.MockTransport(handle), attempts=3)
    with pytest.raises(RuntimeError):
        router.complete(
            "test",
            "system",
            "user",
            DocumentHTML.model_json_schema(),
            100,
            one_shot=True,
        )
    assert len(requests) == 1


def test_api_has_a_hard_overall_deadline():
    import asyncio

    async def hang(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    router = OpenRouter("secret", transport=httpx.MockTransport(hang), deadline=0.01)
    with pytest.raises(RuntimeError, match="overall deadline"):
        router.complete(
            "test",
            "system",
            "user",
            DocumentHTML.model_json_schema(),
            100,
            one_shot=True,
        )
