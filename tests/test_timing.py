import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.config import StudioError
from app.models import Document, Job, database
from app.providers.bodhan import BodhanProvider
from app.services.job_runner import JobRunner
from app.services.timing import JobTiming, current_timing, timed_span


def test_monotonic_duration_and_exception_logging(tmp_path, monkeypatch):
    ticks = iter([10, 11, 14, 17])
    monkeypatch.setattr("app.services.timing.time.perf_counter", lambda: next(ticks))
    log = tmp_path / "events.jsonl"
    timing = JobTiming("job", "doc", "translate", datetime.now(timezone.utc), log)
    token = current_timing.set(timing)
    try:
        with pytest.raises(ValueError), timed_span("translation_http"):
            raise ValueError("private text must not be logged")
        result = timing.snapshot("failed")
    finally:
        current_timing.reset(token)
    assert result["processing_wall_seconds"] == 7
    assert result["category_seconds"] == {"translation_http": 3}
    assert result["events"][0]["exception_type"] == "ValueError"
    assert "private text" not in log.read_text()
    assert all(
        json.loads(line)["job_id"] == "job" for line in log.read_text().splitlines()
    )


@pytest.mark.asyncio
async def test_provider_records_429_and_retry_wait_without_content():
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "translated"}}
                ]
            },
        )

    timing = JobTiming("j", "d", "translate", datetime.now(timezone.utc))
    token = current_timing.set(timing)
    try:
        await BodhanProvider("secret", transport=httpx.MockTransport(handle)).translate(
            "private source", "hi", "native"
        )
    finally:
        current_timing.reset(token)
    result = timing.snapshot("completed")
    assert result["category_counts"]["translation_http"] == 2
    assert result["category_counts"]["translation_retry_wait"] == 1
    assert [
        e["status_code"]
        for e in result["events"]
        if e["category"] == "translation_http"
    ] == [429, 200]
    assert "private source" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_runner_preserves_interrupted_timing_and_isolates_jobs(tmp_path):
    engine, sessions = database(tmp_path / "jobs.sqlite3")
    with sessions() as session:
        for name in ("first", "second"):
            session.add(Document(id=name, filename="x", sha256="x"))
            session.add(Job(id=name, document_id=name, operation="test"))
        session.commit()

    class Orchestrator:
        def __init__(self):
            self.release = asyncio.Event()
            self.started = set()

        async def run(self, job_id, cancel):
            self.started.add(job_id)
            with timed_span(job_id):
                await cancel.wait(self.release.wait())
            if job_id == "second":
                raise StudioError("test", "expected failure")
            return "completed"

    orchestrator = Orchestrator()
    runner = JobRunner(sessions, orchestrator, workers=2)

    async def until(predicate):
        for _ in range(200):
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Job did not reach expected state")

    try:
        await runner.start()
        await until(lambda: len(orchestrator.started) == 2)
        await runner.stop()
        with sessions() as session:
            for job in session.query(Job):
                attempt = job.payload["timing"]["attempts"][0]
                assert attempt["status"] == "interrupted"
                assert set(attempt["category_seconds"]) == {job.id}
        orchestrator.release.set()
        await runner.start()

        def finished():
            with sessions() as session:
                return all(
                    len(j.payload["timing"]["attempts"]) == 2
                    and j.payload["timing"]["attempts"][-1]["status"] != "running"
                    for j in session.query(Job)
                )

        await until(finished)
        with sessions() as session:
            for job in session.query(Job):
                attempts = job.payload["timing"]["attempts"]
                assert len(attempts) == 2
                assert attempts[0]["status"] == "interrupted"
                assert attempts[1]["status"] == (
                    "completed" if job.id == "first" else "failed"
                )
                assert set(attempts[1]["category_seconds"]) == {job.id}
        assert current_timing.get() is None
    finally:
        await runner.stop()
        engine.dispose()
