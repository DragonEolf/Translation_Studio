import asyncio
import time
from threading import Event
import pymupdf as fitz
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.models import Document, Job
from app.services.cancellation import CancellationToken, JobCancelled


def until(client, job_id, states):
    for _ in range(150):
        result=client.get('/v1/jobs/'+job_id).json()
        if result['status'] in states:
            return result
        time.sleep(.02)
    raise AssertionError(result)


class WaitingProvider:
    def __init__(self):
        self.calls=0
        self.entered=Event()
        self.interrupted=Event()
        self.block=True

    async def translate(self,text,language,script):
        self.calls+=1
        self.entered.set()
        if self.block:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.interrupted.set()
                raise
        return {'translation':text,'truncated':False}


def test_cancel_interrupts_api_and_worker_can_process_next_job(tmp_path):
    provider=WaitingProvider()
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path,studio_workers=1),provider=provider)
    with fitz.open() as pdf:
        for _ in range(2):
            pdf.new_page().insert_text((40,70),'Hello world')
        data=pdf.tobytes()
    with TestClient(app) as client:
        upload=client.post('/v1/documents',files={'file':('test.pdf',data,'application/pdf')}).json()
        assert until(client,upload['job_id'],{'completed','failed'})['status']=='completed'
        doc=upload['document_id']
        job=client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}).json()
        assert provider.entered.wait(3)
        # A second queued job is cancelled without ever reaching the provider.
        with app.state.sessions() as session:
            session.add(Document(id='queued-doc',filename='queued.pdf',sha256='q'))
            session.flush()
            session.add(Job(id='queued-job',document_id='queued-doc',operation='translate'))
            session.commit()
        assert client.post('/v1/jobs/queued-job/cancel').json()['status']=='cancelled'
        assert client.post('/v1/jobs/'+job['job_id']+'/cancel').json()['status']=='cancelling'
        assert until(client,job['job_id'],{'cancelled','failed'})['status']=='cancelled'
        assert provider.interrupted.wait(1)
        assert provider.calls==1
        assert not (tmp_path/doc/'final.pdf').exists()
        assert client.post('/v1/jobs/'+job['job_id']+'/cancel').json()['status']=='cancelled'
        provider.block=False
        next_job=client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}).json()
        result=until(client,next_job['job_id'],{'completed','completed_with_issues','failed'})
        assert result['status']=='completed',result
        # Cancelling an already completed job doesn't alter its result.
        assert client.post('/v1/jobs/'+next_job['job_id']+'/cancel').json()['status']=='completed'
        assert client.post('/v1/jobs/missing/cancel').status_code==404


def test_render_cancellation_stops_before_any_page_output(tmp_path):
    from app.services.cancellation import CancellationToken
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path))
    token=CancellationToken();token.cancel()
    try:
        app.state.orchestrator.render('does-not-exist',cancel=token)
        raise AssertionError('Expected cancellation')
    except JobCancelled:
        pass
    assert not list(tmp_path.rglob('page-*.pdf'))


def test_cancelling_pdf_work_keeps_document_locked_until_worker_stops(tmp_path):
    provider=WaitingProvider();provider.block=False
    app=create_app(Settings(_env_file=None,studio_data_dir=tmp_path),provider=provider)
    entered,release=Event(),Event()
    original=app.state.orchestrator.render
    def paused_render(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    app.state.orchestrator.render=paused_render
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40,70),'Hello world')
        data=pdf.tobytes()
    with TestClient(app) as client:
        upload=client.post('/v1/documents',files={'file':('test.pdf',data,'application/pdf')}).json()
        until(client,upload['job_id'],{'completed'})
        doc=upload['document_id']
        job=client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}).json()
        try:
            assert entered.wait(3)
            assert client.post('/v1/jobs/'+job['job_id']+'/cancel').json()['status']=='cancelling'
            assert client.post(f'/v1/documents/{doc}/translations',json={'target_language':'en'}).status_code==409
        finally:
            release.set()
        assert until(client,job['job_id'],{'cancelled','failed'})['status']=='cancelled'
        assert not (tmp_path/doc/'final.pdf').exists()
