"""Single-process asynchronous job queue with restart recovery and cancellation."""

import asyncio
import logging

from sqlalchemy import select

from app.config import StudioError
from app.models import Document, Job
from app.services.cancellation import CancellationToken, JobCancelled
from app.services.timing import JobTiming, current_timing


class JobRunner:
    def __init__(self, sessions, orchestrator, workers=4):
        self.sessions, self.orchestrator = sessions, orchestrator
        self.workers = workers
        self.tasks = []
        self.active = {}

    async def start(self):
        with self.sessions() as session:
            for job in session.scalars(select(Job).where(Job.status == "running")):
                job.status = "queued"
            for job in session.scalars(select(Job).where(Job.status == "cancelling")):
                job.status = "cancelled"
                session.get(Document, job.document_id).status = "cancelled"
            session.commit()
        self.tasks = [asyncio.create_task(self.loop()) for _ in range(self.workers)]

    def cancel(self, job_id):
        token = self.active.get(job_id)
        if token:
            token.cancel()

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []

    async def loop(self):
        while True:
            with self.sessions() as session:
                job = session.scalar(
                    select(Job).where(Job.status == "queued").order_by(Job.created_at)
                )
                job_id = job.id if job else None
                if job:
                    job.status = "running"
                    session.commit()
            if not job_id:
                await asyncio.sleep(0.1)
                continue
            token = CancellationToken()
            self.active[job_id] = token
            storage = getattr(self.orchestrator, "storage", None)
            timing = JobTiming(
                job_id,
                job.document_id,
                job.operation,
                job.created_at,
                storage.root / "timing-events.jsonl" if storage else None,
            )
            context_token = current_timing.set(timing)
            with self.sessions() as session:
                current = session.get(Job, job_id)
                previous = current.payload.get("timing", {})
                attempts = previous.get("attempts", [])
                # A process can end before its final timing checkpoint.
                attempts = [
                    {**a, "status": "interrupted"} if a["status"] == "running" else a
                    for a in attempts
                ]
                current.payload = {
                    **current.payload,
                    "timing": {
                        "version": 1,
                        "had_unmeasured_progress": previous.get(
                            "had_unmeasured_progress", bool(current.progress)
                        ),
                        "attempts": [*attempts, timing.snapshot()],
                    },
                }
                session.commit()
            timing_status = "interrupted"
            try:
                status = await self.orchestrator.run(job_id, token)
                token.check()
                with self.sessions() as session:
                    job = session.get(Job, job_id)
                    job.status, job.progress = status, 1
                    session.get(Document, job.document_id).status = status
                    session.commit()
                timing_status = status
            except JobCancelled:
                with self.sessions() as session:
                    job = session.get(Job, job_id)
                    job.status = "cancelled"
                    session.get(Document, job.document_id).status = "cancelled"
                    session.commit()
                timing_status = "cancelled"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not isinstance(exc, StudioError):
                    logging.getLogger(__name__).exception(
                        "PDF processing failed for job %s", job_id
                    )
                with self.sessions() as session:
                    job = session.get(Job, job_id)
                    job.status = "cancelled" if token.event.is_set() else "failed"
                    job.error_code = (
                        exc.code
                        if isinstance(exc, StudioError)
                        else "processing_failed"
                    )
                    job.error_message = (
                        exc.message
                        if isinstance(exc, StudioError)
                        else "Processing failed; inspect server diagnostics"
                    )
                    session.get(Document, job.document_id).status = job.status
                    timing_status = job.status
                    session.commit()

            finally:
                result = timing.snapshot(timing_status)
                timing.write({"event": "job_finished", **result})
                with self.sessions() as session:
                    current = session.get(Job, job_id)
                    metadata = current.payload["timing"]
                    current.payload = {
                        **current.payload,
                        "timing": {
                            **metadata,
                            "attempts": [
                                result if a["attempt_id"] == timing.attempt_id else a
                                for a in metadata["attempts"]
                            ],
                        },
                    }
                    session.commit()
                current_timing.reset(context_token)
                self.active.pop(job_id, None)
