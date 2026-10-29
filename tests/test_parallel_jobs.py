import asyncio

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Document, Job, database
from app.services.job_runner import JobRunner


def test_parallel_jobs_and_independent_cancellation(tmp_path):
    async def exercise():
        engine, sessions = database(tmp_path / 'test.sqlite3')
        with sessions() as session:
            for i in range(3):
                session.add(Document(id=str(i), filename='x', sha256='x'))
                session.add(Job(id=str(i), document_id=str(i), operation='test'))
            session.commit()

        class Orchestrator:
            def __init__(self):
                self.started = set()
                self.release = asyncio.Event()

            async def run(self, job_id, cancel):
                self.started.add(job_id)
                await cancel.wait(self.release.wait())
                return 'completed'

        orchestrator = Orchestrator()
        runner = JobRunner(sessions, orchestrator, workers=2)
        await runner.start()
        try:
            for _ in range(100):
                if len(orchestrator.started) == 2:
                    break
                await asyncio.sleep(.01)
            assert len(orchestrator.started) == 2
            cancelled = next(iter(orchestrator.started))
            runner.cancel(cancelled)
            for _ in range(100):
                if len(orchestrator.started) == 3:
                    break
                await asyncio.sleep(.01)
            assert len(orchestrator.started) == 3
            orchestrator.release.set()
            for _ in range(100):
                with sessions() as session:
                    statuses = {j.id: j.status for j in session.query(Job).all()}
                if all(s in {'completed', 'cancelled'} for s in statuses.values()):
                    break
                await asyncio.sleep(.01)
            assert statuses[cancelled] == 'cancelled'
            assert sorted(statuses.values()) == ['cancelled', 'completed', 'completed']
        finally:
            await runner.stop()
            engine.dispose()
    asyncio.run(exercise())


def test_database_rejects_two_active_jobs_for_document(tmp_path):
    engine, sessions = database(tmp_path / 'test.sqlite3')
    with sessions() as session:
        session.add(Document(id='doc', filename='x', sha256='x'))
        session.add(Job(id='first', document_id='doc', operation='test'))
        session.commit()
        session.add(Job(id='second', document_id='doc', operation='test'))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.get(Job, 'first').status = 'completed'
        session.add(Job(id='second', document_id='doc', operation='test'))
        session.commit()
    engine.dispose()
