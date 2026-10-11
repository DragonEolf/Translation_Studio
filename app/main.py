import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, UploadFile, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.config import Settings, StudioError
from app.models import database, Document, Page, Block, Translation, Issue, Job, Artifact, LayoutResult, uid
from app.agents.translator import source_hash
from app.orchestrator import Orchestrator, latest
from app.providers.bodhan import BodhanProvider, validate_target, LANGUAGES
from app.providers.reasoning import DisabledReasoningProvider
from app.providers.ocr import BodhanOCRProvider
from app.services.storage import Storage
from app.services.text_fitter import FontRegistry
from app.services.job_runner import JobRunner


class TranslateRequest(BaseModel):
    target_language: str
    script: str = "native"


class EditRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50000)


class ReviseRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=10000)


def record(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def create_app(settings=None, provider=None, reasoning=None, ocr_provider=None):
    settings = settings or Settings()
    storage = Storage(settings.studio_data_dir)
    engine, sessions = database(storage.root / "studio.sqlite3")
    fonts = FontRegistry(settings.studio_font_dir)
    reasoning = reasoning or DisabledReasoningProvider()
    orchestrator = Orchestrator(sessions, storage, settings, provider or BodhanProvider(settings.bodhan_api_key, settings.studio_provider_attempts), fonts, reasoning, ocr_provider or BodhanOCRProvider(settings.bodhan_ocr_api_key or settings.bodhan_api_key, settings.studio_provider_attempts))
    runner = JobRunner(sessions, orchestrator)

    @asynccontextmanager
    async def lifespan(app):
        await runner.start()
        yield
        await runner.stop()
        engine.dispose()

    app = FastAPI(title="Translation Studio", version="0.1.0", lifespan=lifespan)
    app.state.sessions = sessions
    app.state.orchestrator = orchestrator

    @app.exception_handler(StudioError)
    async def error_handler(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        return JSONResponse({"error": {"code": "invalid_request", "message": "Request validation failed"}}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_handler(request, exc):
        return JSONResponse({"error": {"code": "not_found" if exc.status_code == 404 else "http_error", "message": str(exc.detail)}}, status_code=exc.status_code)

    def require(session, model, identity):
        obj = session.get(model, identity)
        if not obj:
            raise StudioError("not_found", "Resource not found", 404)
        return obj

    def enqueue(session, document, operation, payload=None, commit=True):
        if session.scalar(select(Job.id).where(Job.document_id == document.id, Job.status.in_(["queued", "running"]))):
            raise StudioError("document_busy", "A job is already active for this document", 409)
        job = Job(document_id=document.id, operation=operation, payload=payload or {})
        document.status = "queued"
        session.add(job)
        if commit:
            session.commit()
        else:
            session.flush()
        return {"document_id": document.id, "job_id": job.id, "status": job.status}

    @app.get("/v1/health")
    def health():
        return {"status": "ok", "reasoning_enabled": not isinstance(reasoning, DisabledReasoningProvider), "font_scripts": sorted(fonts.paths), "supported_languages": sorted(LANGUAGES)}

    @app.post("/v1/documents", status_code=202)
    async def upload(file: UploadFile) -> dict:
        identity = uid()
        path = storage.path(identity, "source.pdf")
        digest, total = hashlib.sha256(), 0
        try:
            with path.open("wb") as output:
                while chunk := await file.read(1024*1024):
                    total += len(chunk)
                    if total > settings.studio_max_upload_mb * 1024*1024:
                        raise StudioError("upload_too_large", "PDF exceeds upload size limit", 413)
                    digest.update(chunk)
                    output.write(chunk)
            if total == 0 or not path.read_bytes()[:1024].lstrip().startswith(b"%PDF-"):
                raise StudioError("invalid_pdf", "Upload a PDF file")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        with sessions() as session:
            document = Document(id=identity, filename=Path(file.filename or "document.pdf").name, sha256=digest.hexdigest())
            session.add(document)
            session.flush()
            session.add(Artifact(document_id=identity, kind="source", path=str(path)))
            return enqueue(session, document, "inspect")

    @app.get("/v1/documents/{document_id}")
    def document(document_id: str) -> dict:
        with sessions() as session:
            data = record(require(session, Document, document_id))
            data["issue_summary"] = [record(i) for i in session.scalars(select(Issue).where(Issue.document_id == document_id, Issue.resolution != "resolved"))]
            active=[b for b in session.scalars(select(Block).where(Block.document_id==document_id)) if not b.style.get("retired")]
            translated=sum(bool(latest(session,b.id,data["target_language"],data["script"])) for b in active) if data["target_language"] else 0
            fitted=sum(bool((l:=session.get(LayoutResult,b.id)) and l.fit_status=="fitted" and latest(session,b.id,data["target_language"],data["script"])) for b in active) if data["target_language"] else 0
            data["translation_summary"]={"total_blocks":len(active),"translated_blocks":translated,"rendered_blocks":fitted,"retained_source_blocks":len(active)-fitted}
            return data

    @app.get("/v1/documents/{document_id}/pages")
    def pages(document_id: str) -> list[dict]:
        with sessions() as session:
            require(session, Document, document_id)
            return [dict(record(p), source_preview=f"/v1/documents/{document_id}/artifacts/source-{p.number}.png", translated_preview=f"/v1/documents/{document_id}/artifacts/page-{p.number}.png") for p in session.scalars(select(Page).where(Page.document_id == document_id).order_by(Page.number))]

    @app.get("/v1/documents/{document_id}/blocks")
    def blocks(document_id: str, page_number: int | None = None, type: str | None = None) -> list[dict]:
        with sessions() as session:
            doc = require(session, Document, document_id)
            query = select(Block).where(Block.document_id == document_id)
            if page_number:
                query = query.where(Block.page_number == page_number)
            if type:
                query = query.where(Block.type == type)
            result = []
            for block in session.scalars(query.order_by(Block.page_number, Block.reading_order)):
                if block.style.get("retired"):
                    continue
                translation = latest(session, block.id, doc.target_language, doc.script)
                result.append(dict(record(block), translation=record(translation) if translation else None))
            return result

    @app.post("/v1/documents/{document_id}/translations", status_code=202)
    def translate(document_id: str, body: TranslateRequest) -> dict:
        validate_target(body.target_language, body.script)
        with sessions() as session:
            doc = require(session, Document, document_id)
            if not doc.page_count:
                raise StudioError("document_not_ready", "Inspection must complete first", 409)
            return enqueue(session, doc, "translate", body.model_dump())

    @app.get("/v1/jobs/{job_id}")
    def job(job_id: str) -> dict:
        with sessions() as session:
            return record(require(session, Job, job_id))

    def edit(session, block, text):
        doc = require(session, Document, block.document_id)
        previous = latest(session, block.id, doc.target_language, doc.script)
        if not doc.target_language:
            raise StudioError("translation_not_ready", "Translate this block before editing", 409)
        result = enqueue(session, doc, "render", commit=False)
        history = latest(session, block.id)
        session.add(Translation(block_id=block.id, target_language=doc.target_language, script=doc.script, text=text, revision=history.revision+1 if history else 1, metadata_json={"edited": True,"source_hash":source_hash(block.source_text)}))
        for issue in session.scalars(select(Issue).where(Issue.block_id == block.id, Issue.stage == "translator")):
            issue.resolution = "resolved"
        session.get(Page, f"{doc.id}-p{block.page_number}").dirty = True
        session.commit()
        return result

    @app.put("/v1/blocks/{block_id}/translation", status_code=202)
    def replace_translation(block_id: str, body: EditRequest):
        with sessions() as session:
            return edit(session, require(session, Block, block_id), body.text)

    @app.post("/v1/blocks/{block_id}/revise", status_code=202)
    async def revise(block_id: str, body: ReviseRequest):
        with sessions() as session:
            block = require(session, Block, block_id)
            translation = latest(session, block.id)
            text = await reasoning.revise(body.instruction, {"block": record(block), "translation": record(translation) if translation else None})
            return edit(session, block, text)

    @app.post("/v1/documents/{document_id}/render", status_code=202)
    def render(document_id: str):
        with sessions() as session:
            doc = require(session, Document, document_id)
            if not doc.target_language:
                raise StudioError("translation_not_ready", "Translate document first", 409)
            return enqueue(session, doc, "render")

    @app.get("/v1/documents/{document_id}/issues")
    def issues(document_id: str, resolution: str | None = None):
        with sessions() as session:
            require(session, Document, document_id)
            query = select(Issue).where(Issue.document_id == document_id)
            if resolution:
                query = query.where(Issue.resolution == resolution)
            return [record(i) for i in session.scalars(query)]

    @app.get("/v1/documents/{document_id}/artifacts/{filename}")
    def artifact(document_id: str, filename: str):
        with sessions() as session:
            require(session, Document, document_id)
        if filename not in {"final.pdf", "draft.pdf", "structure.json"} and not __import__("re").fullmatch(r"(?:source|page)-\d+\.png", filename):
            raise StudioError("not_found", "Artifact not found", 404)
        path = storage.path(document_id, filename)
        if not path.exists():
            raise StudioError("artifact_not_ready", "Artifact is not available", 404)
        return FileResponse(path, filename=filename)

    return app
