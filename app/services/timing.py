"""Content-free job timing with UTC timestamps and monotonic durations."""

import json
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

current_timing = ContextVar("studio_job_timing", default=None)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class JobTiming:
    def __init__(self, job_id, document_id, operation, queued_at, log_path=None):
        self.attempt_id = uuid4().hex
        self.job_id = job_id
        self.document_id = document_id
        self.operation = operation
        self.started_at = utc_now()
        self.start = time.perf_counter()
        self.log_path = log_path
        self.events = []
        self.lock = RLock()
        if queued_at.tzinfo is None:
            queued_at = queued_at.replace(tzinfo=timezone.utc)
        self.queue_elapsed_seconds = max(
            0, (datetime.fromisoformat(self.started_at) - queued_at).total_seconds()
        )
        self.write({"event": "job_started", "at": self.started_at})

    def write(self, event):
        if self.log_path is not None:
            with self.lock, self.log_path.open("a", encoding="utf-8") as output:
                output.write(
                    json.dumps(
                        {
                            "job_id": self.job_id,
                            "document_id": self.document_id,
                            "operation": self.operation,
                            "attempt_id": self.attempt_id,
                            **event,
                        }
                    )
                    + "\n"
                )

    def snapshot(self, status="running"):
        totals = Counter()
        counts = Counter()
        for event in self.events:
            totals[event["category"]] += event["seconds"]
            counts[event["category"]] += 1
        return {
            "attempt_id": self.attempt_id,
            "started_at": self.started_at,
            "finished_at": utc_now() if status != "running" else None,
            "status": status,
            "processing_wall_seconds": time.perf_counter() - self.start,
            "queue_elapsed_seconds": self.queue_elapsed_seconds,
            "category_seconds": dict(totals),
            "category_counts": dict(counts),
            "events": list(self.events),
        }


@contextmanager
def timed_span(category, **metadata):
    """Measure one stage; no document text, images, URLs or keys are logged."""
    recorder = current_timing.get()
    if recorder is None:
        yield {}
        return
    started_at, start = utc_now(), time.perf_counter()
    details = dict(metadata)
    recorder.write(
        {"event": "span_started", "at": started_at, "category": category, **details}
    )
    try:
        yield details
    except BaseException as exc:
        details["exception_type"] = type(exc).__name__
        raise
    finally:
        event = {
            "category": category,
            "started_at": started_at,
            "finished_at": utc_now(),
            "seconds": time.perf_counter() - start,
            **details,
        }
        with recorder.lock:
            recorder.events.append(event)
            recorder.write({"event": "span_finished", **event})
