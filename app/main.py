import hashlib
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, UploadFile, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.config import Settings, StudioError
from app.models import database, Document, Page, Block, Translation, Issue, Job, Artifact, LayoutResult, uid
from app.agents.translator import source_hash
from app.services.rich_text import rich, format_hash
from app.orchestrator import Orchestrator, latest, pdf_thread
from app.services.document_review import review_snapshot
from app.providers.bodhan import BodhanProvider, validate_target, LANGUAGES, LANGUAGE_NAMES
from app.providers.ocr import BodhanOCRProvider
from app.services.storage import Storage
from app.services.text_fitter import FontRegistry
from app.services.job_runner import JobRunner
from app.providers.transliteration import BodhanTransliterationProvider, OpenRouterPhoneticProvider
from app.services import transliteration as translit


class TranslateRequest(BaseModel):
    target_language: str
    script: str = "native"
    layout_mode: Literal["spans", "paragraphs", "auto"] = "spans"
    transliterate: bool = False


class TransliterationPreviewRequest(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    words: list[str] = Field(min_length=1, max_length=20)


class TransliterationApplyRequest(BaseModel):
    preview_job_id: str
    words: list[str] = Field(min_length=1, max_length=20)


class EditRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50000)


class RepairRequest(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    region_ids: list[str] = Field(min_length=1, max_length=100)
    instruction: str = Field(default="", max_length=2000)


class ReviseRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=10000)


def record(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def create_app(settings=None, provider=None, reasoning=None, ocr_provider=None, vision_reviewer=None, transliteration_provider=None, repair_provider=None):
    settings = settings or Settings()
    storage = Storage(settings.studio_data_dir)
    engine, sessions = database(storage.root / "studio.sqlite3")
    fonts = FontRegistry(settings.studio_font_dir)
    # Legacy repair injection arguments remain unused; selected-region repair
    # uses the separately configured repair_provider below.
    reasoning = None
    orchestrator = Orchestrator(sessions, storage, settings, provider or BodhanProvider(settings.bodhan_api_key, settings.studio_provider_attempts), fonts, reasoning, ocr_provider or BodhanOCRProvider(settings.bodhan_ocr_api_key or settings.bodhan_api_key, settings.studio_provider_attempts))
    phonetic_fallback = (OpenRouterPhoneticProvider(settings.openrouter_api_key,settings.studio_transliteration_model)
                         if settings.studio_transliteration_fallback_enabled and settings.openrouter_api_key else None)
    orchestrator.transliterator = transliteration_provider or BodhanTransliterationProvider(settings.bodhan_api_key, settings.studio_provider_attempts, fallback=phonetic_fallback)
    from app.providers.repair import AIRepairProvider
    orchestrator.repair_provider = repair_provider or AIRepairProvider(settings)
    runner = JobRunner(sessions, orchestrator, settings.studio_workers)

    @asynccontextmanager
    async def lifespan(app):
        await runner.start()
        yield
        await runner.stop()
        engine.dispose()

    app = FastAPI(title="Translation Studio", version="0.1.0", lifespan=lifespan)
    app.state.sessions = sessions
    app.state.orchestrator = orchestrator
    web = Path(__file__).parent / "web"
    app.mount("/assets", StaticFiles(directory=web), name="assets")

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(web / "index.html")

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
        if session.scalar(select(Job.id).where(Job.document_id == document.id, Job.status.in_(["queued", "running", "cancelling"]))):
            raise StudioError("document_busy", "A job is already active for this document", 409)
        job = Job(document_id=document.id, operation=operation, payload=payload or {})
        document.status = "queued"
        session.add(job)
        try:
            session.flush()
            if commit:
                session.commit()
        except IntegrityError as exc:
            session.rollback()
            if "jobs.document_id" not in str(exc.orig):
                raise
            raise StudioError("document_busy", "A job is already active for this document", 409) from exc
        return {"document_id": document.id, "job_id": job.id, "status": job.status}

    @app.get("/v1/health")
    def health():
        return {"status": "ok", "font_scripts": sorted(fonts.paths), "supported_languages": sorted(LANGUAGES), "max_upload_mb":settings.studio_max_upload_mb, "max_pages":settings.studio_max_pages,
                "repair_configured": repair_provider is not None or bool(settings.openrouter_api_key and settings.studio_repair_model),
                "repair_max_regions": settings.studio_repair_max_regions,
                "layout_policy_version":1, "transliteration_fallback_configured":phonetic_fallback is not None,
                "transliteration_configured":bool(settings.bodhan_api_key) or phonetic_fallback is not None or transliteration_provider is not None, "translation_configured": bool(settings.bodhan_api_key) or provider is not None,
                "ocr_configured": bool(settings.bodhan_ocr_api_key or settings.bodhan_api_key) or ocr_provider is not None,
                "available_font_languages": sorted(language for language in LANGUAGES if fonts.resolve(language, "native") != "missing")}

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
            # Let the page recover a lost translation-submission response without
            # posting the same billable operation again.
            last_job = session.scalar(select(Job).where(Job.document_id == document_id).order_by(Job.created_at.desc(), Job.id.desc()))
            data["latest_job"] = {key: getattr(last_job, key) for key in ("id", "operation", "status")} if last_job else None
            return data

    @app.get("/v1/documents/{document_id}/pages")
    def pages(document_id: str) -> list[dict]:
        with sessions() as session:
            require(session, Document, document_id)
            return [dict(record(p), source_preview=f"/v1/documents/{document_id}/artifacts/source-{p.number}.png", translated_preview=f"/v1/documents/{document_id}/artifacts/page-{p.number}.png") for p in session.scalars(select(Page).where(Page.document_id == document_id).order_by(Page.number))]

    @app.get("/v1/documents/{document_id}/review")
    async def review(document_id: str) -> dict:
        # Share the PDF lock with rendering; never call a provider for review.
        return await pdf_thread(review_snapshot, sessions, storage, document_id)

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
        if body.transliterate and (body.target_language == "en" or body.script != "native"):
            raise StudioError("transliteration_not_applicable", "Automatic transliteration requires a native Indic target script", 422)
        with sessions() as session:
            doc = require(session, Document, document_id)
            if not doc.page_count:
                raise StudioError("document_not_ready", "Inspection must complete first", 409)
            return enqueue(session, doc, "translate", body.model_dump())

    def transliteration_entries(session, doc):
        if not doc.target_language or doc.target_language == "en" or doc.script != "native":
            raise StudioError("transliteration_not_applicable", "Translate into a native Indic script first", 422)
        entries=[]
        for block in session.scalars(select(Block).where(Block.document_id==doc.id).order_by(Block.page_number,Block.reading_order)):
            if block.style.get('retired'):
                continue
            translation=latest(session,block.id,doc.target_language,doc.script)
            layout=session.get(LayoutResult,block.id)
            if translation and layout and layout.fit_status=='fitted':
                entries.append((block,translation))
        return entries

    @app.get("/v1/documents/{document_id}/transliteration-candidates")
    def transliteration_candidates(document_id: str):
        with sessions() as session:
            doc=require(session,Document,document_id)
            return {"candidates":translit.candidates(transliteration_entries(session,doc)),"max_words":20}

    @app.post("/v1/documents/{document_id}/transliteration-preview", status_code=202)
    def preview_transliteration(document_id: str, body: TransliterationPreviewRequest):
        with sessions() as session:
            doc=require(session,Document,document_id)
            if body.request_id:
                previous=session.scalar(select(Job).where(Job.document_id==doc.id,Job.operation=='transliterate_preview',Job.payload['request_id'].as_string()==body.request_id))
                if previous:
                    if sorted(set(body.words))!=sorted(previous.payload['words']):
                        raise StudioError('invalid_transliteration_selection','Use a fresh request ID for a new selection',422)
                    return {'document_id':doc.id,'job_id':previous.id,'status':previous.status}
            entries=transliteration_entries(session,doc)
            offered={item['word'] for item in translit.candidates(entries)}
            words=list(dict.fromkeys(body.words))
            if any(word not in offered for word in words):
                raise StudioError('invalid_transliteration_selection','Choose words from the current translated document',422)
            return enqueue(session,doc,'transliterate_preview',{'words':words,'request_id':body.request_id,'target_language':doc.target_language,
                'snapshot':translit.snapshot(entries)})

    @app.post("/v1/documents/{document_id}/transliteration-apply", status_code=202)
    def apply_transliteration(document_id: str, body: TransliterationApplyRequest):
        with sessions() as session:
            doc=require(session,Document,document_id)
            preview=require(session,Job,body.preview_job_id)
            if preview.document_id!=doc.id or preview.operation!='transliterate_preview' or preview.status!='completed':
                raise StudioError('invalid_transliteration_preview','Generate a completed preview for this document first',409)
            previous_job=preview.payload.get('applied_job_id')
            if previous_job:
                if sorted(set(body.words))!=preview.payload.get('applied_words'):
                    raise StudioError('transliteration_already_applied','This preview was already applied. Generate a new preview to change the selection.',409)
                existing=require(session,Job,previous_job)
                return {'document_id':doc.id,'job_id':existing.id,'status':existing.status}
            entries=transliteration_entries(session,doc)
            if doc.target_language!=preview.payload['target_language'] or translit.snapshot(entries)!=preview.payload['snapshot']:
                raise StudioError('stale_transliteration_preview','The translation changed. Generate a fresh preview.',409)
            offered={item['word']:item['replacement'] for item in preview.payload.get('suggestions',[])}
            if any(word not in offered for word in body.words):
                raise StudioError('invalid_transliteration_selection','Choose pronunciations from the generated preview',422)
            replacements={word:offered[word] for word in body.words}
            result=enqueue(session,doc,'render',commit=False)
            for block,translation in entries:
                text,metadata=translit.replace(translation,replacements)
                if text==translation.text:
                    continue
                history=latest(session,block.id)
                session.add(Translation(block_id=block.id,target_language=doc.target_language,script=doc.script,text=text,
                    revision=history.revision+1,metadata_json={**metadata,'transliteration_preview_id':preview.id}))
                session.get(Page,f'{doc.id}-p{block.page_number}').dirty=True
            preview.payload={**preview.payload,'applied_job_id':result['job_id'],'applied_words':sorted(set(body.words))}
            session.commit()
            return result

    @app.get("/v1/jobs/{job_id}")
    def job(job_id: str) -> dict:
        with sessions() as session:
            return record(require(session, Job, job_id))

    @app.post("/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict:
        # Runs on the queue's event loop so claim/cancel cannot race each other.
        with sessions() as session:
            job = require(session, Job, job_id)
            if job.status in {"queued", "running", "cancelling"}:
                job.status = "cancelled" if job.status == "queued" else "cancelling"
                require(session, Document, job.document_id).status = job.status
                session.commit()
                runner.cancel(job_id)
            return record(job)

    def edit(session, block, text):
        doc = require(session, Document, block.document_id)
        previous = latest(session, block.id, doc.target_language, doc.script)
        if not doc.target_language:
            raise StudioError("translation_not_ready", "Translate this block before editing", 409)
        if block.style.get('rich_text') and any(r['style_id']!=block.style['rich_text']['base_style_id'] for r in block.style['rich_text']['runs']):
            raise StudioError('formatted_edit_requires_markup','This paragraph has inline formatting. Switch to span mode to edit individual formatted spans.',422)
        result = enqueue(session, doc, "render", commit=False)
        history = latest(session, block.id)
        session.add(Translation(block_id=block.id, target_language=doc.target_language, script=doc.script, text=text, revision=history.revision+1 if history else 1, metadata_json={"edited": True,"source_hash":source_hash(block.source_text),**({"format_hash":format_hash(block)} if rich(block) else {})}))
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
        raise StudioError("reasoning_disabled", "Automatic revision is disabled. Edit the translation directly.", 410)

    @app.post("/v1/documents/{document_id}/review/repair", status_code=202)
    async def repair(document_id: str, body: RepairRequest):
        ids = list(dict.fromkeys(body.region_ids))
        if len(ids) > settings.studio_repair_max_regions:
            raise StudioError("repair_limit", f"Select at most {settings.studio_repair_max_regions} regions per repair.", 422)
        with sessions() as session:
            doc = require(session, Document, document_id)
            for job in session.scalars(select(Job).where(Job.document_id == document_id, Job.operation == "repair")):
                if job.payload.get('request_id') == body.request_id:
                    if (job.payload.get('version'), job.payload.get('region_ids'), job.payload.get('instruction')) != (body.version, ids, body.instruction):
                        raise StudioError("repair_request_conflict", "This repair request ID was already used with different selections.", 409)
                    return {"document_id":document_id, "job_id":job.id, "status":job.status}
            if repair_provider is None and not (settings.openrouter_api_key and settings.studio_repair_model):
                raise StudioError("repair_not_configured", "Set OPENROUTER_API_KEY and STUDIO_REPAIR_MODEL to enable AI repair.", 503)
            snapshot = await pdf_thread(review_snapshot, sessions, storage, document_id)
            if snapshot['version'] != body.version:
                raise StudioError("stale_review", "The output has changed. Reload the review before requesting repairs.", 409)
            known = {r['id'] for r in snapshot['regions']}
            if any(identity not in known for identity in ids):
                raise StudioError("invalid_repair_region", "One or more selected regions do not belong to this output.", 422)
            return enqueue(session, doc, "repair", {**body.model_dump(), 'region_ids':ids, 'output':snapshot['output']})

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
            doc = require(session, Document, document_id)
            original_name, language = doc.filename, doc.target_language
        if filename not in {"final.pdf", "draft.pdf", "previous.pdf", "repair-report.json", "structure.json", "translation.xlf", "layout-report.json"} and not __import__("re").fullmatch(r"(?:source|page)-\d+\.png", filename):
            raise StudioError("not_found", "Artifact not found", 404)
        path = storage.path(document_id, filename)
        if not path.exists():
            raise StudioError("artifact_not_ready", "Artifact is not available", 404)
        download_name = filename
        if filename in {"final.pdf","draft.pdf"}:
            safe_name=re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]',"_",original_name)
            stem=Path(safe_name).stem.strip(" .")[:120] or "document"
            target=f" - {LANGUAGE_NAMES.get(language,language)}" if language else ""
            draft=" - draft" if filename=="draft.pdf" else ""
            download_name=f"{stem}{target}{draft}.pdf"
        return FileResponse(path, filename=download_name)

    return app
