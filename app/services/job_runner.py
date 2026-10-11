import asyncio
import logging
from sqlalchemy import select
from app.models import Job, Document
from app.config import StudioError


class JobRunner:
    def __init__(self, sessions, orchestrator):
        self.sessions, self.orchestrator = sessions, orchestrator
        self.task = None

    async def start(self):
        with self.sessions() as session:
            for job in session.scalars(select(Job).where(Job.status == "running")):
                job.status = "queued"
            session.commit()
        self.task = asyncio.create_task(self.loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

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
            try:
                status = await self.orchestrator.run(job_id)
                with self.sessions() as session:
                    job = session.get(Job, job_id)
                    job.status, job.progress = status, 1
                    session.get(Document, job.document_id).status = status
                    session.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not isinstance(exc, StudioError):
                    logging.getLogger(__name__).exception("PDF processing failed for job %s", job_id)
                with self.sessions() as session:
                    job = session.get(Job, job_id)
                    job.status = "failed"
                    job.error_code = exc.code if isinstance(exc, StudioError) else "processing_failed"
                    job.error_message = exc.message if isinstance(exc, StudioError) else "Processing failed; inspect server diagnostics"
                    session.get(Document, job.document_id).status = "failed"
                    session.commit()
