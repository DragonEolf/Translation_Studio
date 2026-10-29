import asyncio
import logging
from sqlalchemy import select
from app.models import Job, Document
from app.config import StudioError
from app.services.cancellation import CancellationToken, JobCancelled


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
                job = session.scalar(select(Job).where(Job.status == "queued").order_by(Job.created_at))
                job_id = job.id if job else None
                if job:
                    job.status = "running"
                    session.commit()
            if not job_id:
                await asyncio.sleep(.1)
                continue
            token = CancellationToken()
            self.active[job_id] = token
            try:
                status = await self.orchestrator.run(job_id, token)
                token.check()
                with self.sessions() as session:
                    job = session.get(Job, job_id)
                    job.status, job.progress = status, 1
                    session.get(Document, job.document_id).status = status
                    session.commit()
            except JobCancelled:
                with self.sessions() as session:
                    job = session.get(Job, job_id)
                    job.status = "cancelled"
                    session.get(Document, job.document_id).status = "cancelled"
                    session.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not isinstance(exc, StudioError):
                    logging.getLogger(__name__).exception("PDF processing failed for job %s", job_id)
                with self.sessions() as session:
                    job = session.get(Job, job_id)
                    job.status = "cancelled" if token.event.is_set() else "failed"
                    job.error_code = exc.code if isinstance(exc, StudioError) else "processing_failed"
                    job.error_message = exc.message if isinstance(exc, StudioError) else "Processing failed; inspect server diagnostics"
                    session.get(Document, job.document_id).status = job.status
                    session.commit()

            finally:
                self.active.pop(job_id, None)
