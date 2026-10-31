import json

import httpx
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from test_studio import FakeProvider, pdf_bytes, wait

from app.config import Settings, StudioError
from app.main import create_app
from app.models import Translation
from app.providers.repair import AIRepairProvider, RepairSuggestion


class Repair:
    def __init__(self, text="Hi page 1", fail=False):
        self.calls = []
        self.text = text
        self.fail = fail

    async def suggest(self, context, source, output):
        self.calls.append(context)
        assert source.startswith(b"\x89PNG") and output.startswith(b"\x89PNG")
        if self.fail:
            raise StudioError("repair_provider_unavailable", "Offline", 503)
        return RepairSuggestion(
            block_id=context["block_id"],
            action="repair",
            reason="Correct selected passage",
            text=self.text,
        )


def ready(client):
    upload = client.post(
        "/v1/documents", files={"file": ("input.pdf", pdf_bytes(), "application/pdf")}
    )
    identity = upload.json()["document_id"]
    wait(client, upload)
    wait(
        client,
        client.post(
            f"/v1/documents/{identity}/translations", json={"target_language": "en"}
        ),
    )
    snapshot = client.get(f"/v1/documents/{identity}/review").json()
    body = {
        "version": snapshot["version"],
        "request_id": "repair-one",
        "region_ids": [snapshot["regions"][0]["id"]],
        "instruction": "Fix only this passage",
    }
    return identity, body


def test_selected_repair_publishes_checked_pdf_and_backup(tmp_path):
    provider = Repair()
    app = create_app(
        Settings(_env_file=None, studio_data_dir=tmp_path),
        FakeProvider(),
        repair_provider=provider,
    )
    with TestClient(app) as client:
        identity, body = ready(client)
        before = (tmp_path / identity / "final.pdf").read_bytes()
        untouched = (tmp_path / identity / "page-2.pdf").read_bytes()
        response = client.post(f"/v1/documents/{identity}/review/repair", json=body)
        job = wait(client, response)
        assert job["status"] == "completed_with_issues", job
        assert job["payload"]["accepted"] == 1, job
        assert len(provider.calls) == 1
        assert (tmp_path / identity / "previous.pdf").read_bytes() == before
        assert (tmp_path / identity / "page-2.pdf").read_bytes() == untouched
        with fitz.open(tmp_path / identity / "final.pdf") as pdf:
            assert "Hi page 1" in pdf[0].get_text()
            assert "Welcome page 2" in pdf[1].get_text()
        snapshot = client.get(f"/v1/documents/{identity}/review").json()
        assert snapshot["version"] != body["version"] and snapshot["previous_available"]
        assert any(
            i["code"] == "ai_repair_review_required"
            for i in snapshot["regions"][0]["issues"]
        )
        repeated = client.post(f"/v1/documents/{identity}/review/repair", json=body)
        assert repeated.json()["job_id"] == response.json()["job_id"]
        assert len(provider.calls) == 1
        assert (
            client.post(
                f"/v1/documents/{identity}/review/repair",
                json={**body, "request_id": "stale"},
            ).status_code
            == 409
        )
        assert (
            client.get(f"/v1/documents/{identity}/artifacts/previous.pdf").content
            == before
        )


@pytest.mark.parametrize("fail,text", [(False, "Hi page 99"), (True, "Hi page 1")])
def test_bad_repair_or_outage_keeps_pdf_and_revisions(tmp_path, fail, text):
    provider = Repair(text, fail)
    app = create_app(
        Settings(_env_file=None, studio_data_dir=tmp_path),
        FakeProvider(),
        repair_provider=provider,
    )
    with TestClient(app) as client:
        identity, body = ready(client)
        before = (tmp_path / identity / "final.pdf").read_bytes()
        with app.state.sessions() as session:
            count = len(list(session.query(Translation)))
        job = wait(
            client, client.post(f"/v1/documents/{identity}/review/repair", json=body)
        )
        assert job["status"] == ("failed" if fail else "completed_with_issues"), job
        assert job["payload"]["accepted"] == 0
        assert len(provider.calls) == (1 if fail else 2)
        assert (tmp_path / identity / "final.pdf").read_bytes() == before
        with app.state.sessions() as session:
            assert len(list(session.query(Translation))) == count


@pytest.mark.parametrize("failure", ["font", "rendering"])
def test_rejected_repair_retries_with_feedback_and_publishes_only_valid_revision(
    tmp_path, monkeypatch, failure
):
    from app.services import ai_repair

    original_validate = ai_repair.validate_candidate
    finding = {
        "block_id": "neighbour",
        "code": "collision",
        "severity": "error",
        "message": "Candidate overlaps neighbour",
    }
    checks = []

    def validate(*args):
        checks.append(True)
        if failure == "rendering" and len(checks) == 1:
            raise ai_repair.RepairRejected([finding])
        return original_validate(*args)

    monkeypatch.setattr(ai_repair, "validate_candidate", validate)

    class RetryRepair(Repair):
        async def suggest(self, context, source, output):
            proposal = await super().suggest(context, source, output)
            if len(self.calls) == 1 and failure == "font":
                return proposal.model_copy(update={"font_size": 1})
            return proposal

    provider = RetryRepair()
    app = create_app(
        Settings(_env_file=None, studio_data_dir=tmp_path),
        FakeProvider(),
        repair_provider=provider,
    )
    with TestClient(app) as client:
        identity, body = ready(client)
        before = (tmp_path / identity / "final.pdf").read_bytes()
        with app.state.sessions() as session:
            count = session.query(Translation).count()
        job = wait(
            client, client.post(f"/v1/documents/{identity}/review/repair", json=body)
        )
        assert job["payload"]["accepted"] == 1, job
        assert len(provider.calls) == 2
        first, second = provider.calls
        assert "retry_feedback" not in first
        feedback = second["retry_feedback"]
        assert feedback["previous_proposal"]["text"] == "Hi page 1"
        assert feedback["rejection_reason"] == (
            "Repair makes text too small or too large"
            if failure == "font"
            else "Candidate failed rendering checks"
        )
        assert feedback["rendering_issues"] == ([] if failure == "font" else [finding])
        assert first["current_translation"] == second["current_translation"]
        assert first["bbox"] == second["bbox"]
        assert [
            a["status"] for a in job["payload"]["repair_results"][0]["attempts"]
        ] == ["rejected", "repaired"]
        assert (tmp_path / identity / "previous.pdf").read_bytes() == before
        with app.state.sessions() as session:
            assert session.query(Translation).count() == count + 1


@pytest.mark.parametrize("action", ["no_change", "manual_review"])
def test_non_repair_action_does_not_retry(tmp_path, action):
    class Decline(Repair):
        async def suggest(self, context, source, output):
            self.calls.append(context)
            return RepairSuggestion(
                block_id=context["block_id"], action=action, reason="Leave for review"
            )

    provider = Decline()
    app = create_app(
        Settings(_env_file=None, studio_data_dir=tmp_path),
        FakeProvider(),
        repair_provider=provider,
    )
    with TestClient(app) as client:
        identity, body = ready(client)
        job = wait(
            client, client.post(f"/v1/documents/{identity}/review/repair", json=body)
        )
        assert job["payload"]["accepted"] == 0
        assert job["payload"]["repair_results"][0]["status"] == action
        assert len(provider.calls) == 1


def test_retry_provider_failure_keeps_previous_pdf(tmp_path):
    class OfflineRetry(Repair):
        async def suggest(self, context, source, output):
            self.calls.append(context)
            if len(self.calls) == 2:
                raise StudioError("repair_provider_unavailable", "Offline", 503)
            return RepairSuggestion(
                block_id=context["block_id"],
                action="repair",
                reason="Try larger text",
                font_size=100,
            )

    provider = OfflineRetry()
    app = create_app(
        Settings(_env_file=None, studio_data_dir=tmp_path),
        FakeProvider(),
        repair_provider=provider,
    )
    with TestClient(app) as client:
        identity, body = ready(client)
        before = (tmp_path / identity / "final.pdf").read_bytes()
        job = wait(
            client, client.post(f"/v1/documents/{identity}/review/repair", json=body)
        )
        assert job["status"] == "failed"
        assert job["payload"]["accepted"] == 0
        assert len(provider.calls) == 2
        assert (tmp_path / identity / "final.pdf").read_bytes() == before


def test_repair_context_includes_nearby_text_and_graphic_obstacles(tmp_path):
    from types import SimpleNamespace as N

    from app.services.ai_repair import prepare

    source = tmp_path / "source.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=400)
        image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
        image.clear_with(255)
        page.insert_image(fitz.Rect(105, 70, 120, 90), pixmap=image)
        page.draw_line((30, 100), (130, 100))
        pdf.save(source)

    def entry(identity, box):
        block = N(
            id=identity, bbox=box, source_text=identity, type="text", style={"size": 12}
        )
        translation = N(
            text="Translated " + identity,
            target_language="en",
            script="native",
            metadata_json={},
        )
        layout = N(bbox=box, font_size=12)
        return block, translation, layout

    selected = entry("selected", [50, 50, 100, 70])
    nearby = entry("nearby", [105, 50, 140, 70])
    distant = entry("distant", [300, 300, 350, 320])
    entries = [selected, nearby, distant]
    context, original, output = prepare(
        source,
        source,
        N(number=1, width=400, height=400),
        selected,
        [],
        "Enlarge text",
        entries,
        [e[0] for e in entries],
    )
    assert original.startswith(b"\x89PNG") and output.startswith(b"\x89PNG")
    assert [n["block_id"] for n in context["neighbouring_blocks"]] == ["nearby"]
    neighbour = context["neighbouring_blocks"][0]
    assert neighbour["role"] == "obstacle" and neighbour["read_only"]
    assert neighbour["current_translation"] == "Translated nearby"
    assert {g["kind"] for g in context["nearby_graphics"]} == {
        "image",
        "vector_graphic",
    }
    assert all(g["read_only"] for g in context["nearby_graphics"])
    selected[0].style["ocr"] = True
    selected[2].bbox = [106, 72, 118, 88]
    context, _, _ = prepare(
        source, source, N(number=1, width=400, height=400), selected, [], ""
    )
    assert (
        next(g for g in context["nearby_graphics"] if g["kind"] == "image")["role"]
        == "scan_canvas"
    )


def test_missing_configuration_and_invalid_regions(tmp_path):
    app = create_app(Settings(_env_file=None, studio_data_dir=tmp_path), FakeProvider())
    with TestClient(app) as client:
        identity, body = ready(client)
        assert (
            client.post(f"/v1/documents/{identity}/review/repair", json=body).json()[
                "error"
            ]["code"]
            == "repair_not_configured"
        )
    app = create_app(
        Settings(_env_file=None, studio_data_dir=tmp_path),
        FakeProvider(),
        repair_provider=Repair(),
    )
    with TestClient(app) as client:
        body["region_ids"] = ["another-document"]
        assert (
            client.post(
                f"/v1/documents/{identity}/review/repair", json=body
            ).status_code
            == 422
        )


@pytest.mark.asyncio
async def test_provider_uses_source_output_and_rejects_truncation():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": "{}"}}]
            },
        )

    provider = AIRepairProvider(
        Settings(_env_file=None, openrouter_api_key="secret"),
        httpx.MockTransport(handler),
    )
    with pytest.raises(StudioError):
        await provider.suggest({"block_id": "one"}, b"a", b"b")
    parts = requests[0]["messages"][1]["content"]
    assert len([p for p in parts if p["type"] == "image_url"]) == 2
    assert "untrusted data" in requests[0]["messages"][0]["content"]


def test_ocr_region_can_be_repaired_without_repeating_ocr(tmp_path):
    from test_ocr import FakeOCR, mixed_pdf

    ocr = FakeOCR()
    repair = Repair("Hi scan")
    app = create_app(
        Settings(_env_file=None, studio_data_dir=tmp_path, studio_ocr_dpi=72),
        FakeProvider(),
        ocr_provider=ocr,
        repair_provider=repair,
    )
    with TestClient(app) as client:
        upload = client.post("/v1/documents", files={"file": ("scan.pdf", mixed_pdf())})
        identity = upload.json()["document_id"]
        wait(client, upload)
        wait(
            client,
            client.post(
                f"/v1/documents/{identity}/translations", json={"target_language": "en"}
            ),
        )
        snapshot = client.get(f"/v1/documents/{identity}/review").json()
        block = next(r for r in snapshot["regions"] if r["source_text"] == "Hello scan")
        body = {
            "version": snapshot["version"],
            "request_id": "ocr-repair",
            "region_ids": [block["id"]],
        }
        job = wait(
            client, client.post(f"/v1/documents/{identity}/review/repair", json=body)
        )
        assert job["payload"]["accepted"] == 1, job
        assert ocr.calls == 1
        with fitz.open(tmp_path / identity / "final.pdf") as pdf:
            assert "Hi scan" in pdf[1].get_text()


def test_formatted_repair_must_preserve_run_literals_and_styles(tmp_path):
    from types import SimpleNamespace as N

    from app.services.ai_repair import propose
    from app.services.text_fitter import FontRegistry

    with fitz.open() as doc:
        doc.new_page(width=400, height=200)
        doc.save(tmp_path / "source.pdf")
    runs = [
        {"text": "Total ", "style": {"size": 12, "color": 0}},
        {"text": "1250 INR", "style": {"size": 12, "color": 0, "bold": True}},
    ]
    block = N(
        id="one",
        bbox=[20, 20, 300, 70],
        source_text="Total 1250 INR",
        style={
            "size": 12,
            "color": 0,
            "rich_text": {
                "version": 1,
                "styles": {"plain": runs[0]["style"], "bold": runs[1]["style"]},
                "runs": [
                    {"text": "Total ", "style_id": "plain"},
                    {"text": "1250 INR", "style_id": "bold"},
                ],
            },
        },
    )
    translation = N(
        text="Total 1250 INR",
        target_language="en",
        script="native",
        metadata_json={"rich_text": {"runs": runs}},
    )
    layout = N(
        bbox=[20, 20, 300, 70],
        font="sans-serif",
        font_size=12,
        fit_status="fitted",
        line_breaks=[],
    )
    page = N(width=400, height=200, number=1, source=tmp_path / "source.pdf")
    entry = (block, translation, layout)
    suggestion = RepairSuggestion(
        block_id="one",
        action="repair",
        reason="Correct wording",
        run_texts=["Sum ", "1250 INR"],
    )
    candidate = propose(entry, suggestion, [entry], [block], page, FontRegistry(), 6)
    assert candidate[1].text == "Sum 1250 INR"
    assert candidate[1].metadata_json["rich_text"]["runs"][1]["style"]["bold"]
    assert translation.text == "Total 1250 INR"
    with pytest.raises((ValueError, StudioError)):
        propose(
            entry,
            suggestion.model_copy(update={"run_texts": ["Sum 1250 INR", "amount"]}),
            [entry],
            [block],
            page,
            FontRegistry(),
            6,
        )
    with pytest.raises(ValueError):
        propose(
            entry,
            suggestion.model_copy(update={"text": "Flat text", "run_texts": None}),
            [entry],
            [block],
            page,
            FontRegistry(),
            6,
        )
