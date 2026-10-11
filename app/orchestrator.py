import asyncio
import pymupdf as fitz
from sqlalchemy import select
from app.models import Document, Page, Block, Translation, LayoutResult, Issue, Job, Artifact
from app.agents.inspector import InspectorAgent
from app.agents.text_reader import TextReaderAgent
from app.agents.block_maker import BlockMakerAgent
from app.agents.translator import TranslatorAgent, source_hash
from app.agents.reorganiser import ReorganiserAgent
from app.agents.checker import CheckerAgent
from app.services.renderer import Renderer
from app.config import StudioError
from app.agents.ocr_reader import OCRReaderAgent


def latest(session, block_id, language=None, script=None):
    query = select(Translation).where(Translation.block_id == block_id)
    if language:
        query = query.where(Translation.target_language == language, Translation.script == script)
    block = session.get(Block,block_id)
    for translation in session.scalars(query.order_by(Translation.revision.desc())):
        if not language or translation.metadata_json.get("source_hash",source_hash(block.source_text)) == source_hash(block.source_text):
            return translation
    return None


class Orchestrator:
    def __init__(self, sessions, storage, settings, provider, fonts, reasoning, ocr_provider):
        self.sessions, self.storage, self.settings = sessions, storage, settings
        self.translator = TranslatorAgent(provider)
        self.reorganiser = ReorganiserAgent(fonts)
        self.reasoning = reasoning
        self.ocr_provider = ocr_provider

    async def run(self, job_id):
        with self.sessions() as session:
            job = session.get(Job, job_id)
            document = session.get(Document, job.document_id)
            source = self.storage.path(document.id, "source.pdf")
            if job.operation == "inspect":
                metadata = await asyncio.to_thread(InspectorAgent().run, source, self.settings.studio_max_pages)
                extracted = await asyncio.to_thread(TextReaderAgent().run, source)
                blocks = await asyncio.to_thread(BlockMakerAgent().run, document.id, extracted)
                for issue in session.scalars(select(Issue).where(Issue.document_id==document.id,Issue.stage=="ocr")):
                    issue.resolution = "resolved"
                for item in metadata:
                    number = item["number"]
                    if item["extraction_method"] == "ocr":
                        reader = OCRReaderAgent()
                        png, width, height = await asyncio.to_thread(reader.image, source, number, self.settings.studio_ocr_dpi)
                        cache = self.storage.path(document.id,f"ocr-{number}-{self.settings.studio_ocr_dpi}.json")
                        if cache.exists():
                            import json
                            response = json.loads(cache.read_text(encoding="utf-8"))
                        else:
                            response = await self.ocr_provider.extract(png)
                            reader.blocks(document.id,number,item,response,width,height)
                            self.storage.write_json(document.id,cache.name,response)
                        ocr_blocks = reader.blocks(document.id,number,item,response,width,height)
                        if not ocr_blocks:
                            session.add(Issue(document_id=document.id,page_number=number,block_id=None,code="ocr_no_text",stage="ocr",message="OCR found no transcribable text; page retained"))
                        blocks.extend(ocr_blocks)
                        item["ocr_usage"] = response.get("usage",{})
                        session.add(Issue(document_id=document.id,page_number=number,block_id=None,code="ocr_layout_estimated",severity="warning",stage="ocr",message="Scanned font styles are estimated; text regions use white backgrounds. Verify scan and transcription before final use."))
                    job.progress = .9*number/len(metadata)
                # Restartable inspection replaces only incomplete extraction state.
                existing = set(session.scalars(select(Block.id).where(Block.document_id == document.id)))
                for item in metadata:
                    if not session.get(Page, f"{document.id}-p{item['number']}"):
                        session.add(Page(id=f"{document.id}-p{item['number']}", document_id=document.id, number=item["number"], width=item["width"], height=item["height"], metadata_json=item))
                for item in blocks:
                    if item["id"] not in existing:
                        session.add(Block(**item))
                document.page_count = len(metadata)
                await asyncio.to_thread(self.source_previews, source, document.id)
                structured = self.storage.write_json(document.id, "structure.json", {"pages": metadata, "blocks": blocks})
                session.add(Artifact(document_id=document.id, kind="structure", path=str(structured)))
                session.commit()
                return "completed_with_issues" if session.scalar(select(Issue.id).where(Issue.document_id==document.id,Issue.resolution!="resolved")) else "completed"
            if job.operation == "translate":
                await asyncio.to_thread(self.refresh_geometry,document.id)
                session.expire_all()
                changed_target = (document.target_language, document.script) != (job.payload["target_language"], job.payload["script"])
                if changed_target:
                    for issue in session.scalars(select(Issue).where(Issue.document_id == document.id, Issue.stage == "translator")):
                        issue.resolution = "resolved"
                document.target_language, document.script = job.payload["target_language"], job.payload["script"]
                blocks = [b for b in session.scalars(select(Block).where(Block.document_id == document.id).order_by(Block.page_number, Block.reading_order)) if not b.style.get("retired")]
                session.commit()
                for index, block in enumerate(blocks):
                    if changed_target:
                        session.get(Page, f"{document.id}-p{block.page_number}").dirty = True
                    if not latest(session, block.id, document.target_language, document.script):
                        try:
                            text, metadata = await self.translator.run(block, document.target_language, document.script)
                            previous = latest(session, block.id)
                            session.add(Translation(block_id=block.id, target_language=document.target_language, script=document.script, text=text, revision=previous.revision+1 if previous else 1, metadata_json=metadata))
                            for issue in session.scalars(select(Issue).where(Issue.block_id == block.id, Issue.stage == "translator")):
                                issue.resolution = "resolved"
                        except StudioError as exc:
                            if exc.code not in {"translation_truncated", "protected_value_changed"}:
                                raise
                            session.add(Issue(document_id=document.id, block_id=block.id, page_number=block.page_number, code=exc.code, stage="translator", message=exc.message))
                        session.get(Page, f"{document.id}-p{block.page_number}").dirty = True
                    job.progress = .7 * (index + 1)/max(1,len(blocks))
                    session.commit()
            await asyncio.to_thread(self.render, document.id)
            with self.sessions() as check:
                current = [b for b in check.scalars(select(Block).where(Block.document_id==document.id)) if not b.style.get("retired")]
                fitted = sum(1 for b in current if latest(check,b.id,document.target_language,document.script) and (layout:=check.get(LayoutResult,b.id)) and layout.fit_status=="fitted")
                summary={"total_blocks":len(current),"rendered_blocks":fitted,"retained_source_blocks":len(current)-fitted}
                result_job=check.get(Job,job_id)
                result_job.payload={**result_job.payload,"result":summary}
                check.commit()
                unresolved_issues = list(check.scalars(select(Issue).where(Issue.document_id == document.id, Issue.resolution != "resolved")))
                for issue in unresolved_issues:
                    block = check.get(Block, issue.block_id) if issue.block_id else None
                    await self.reasoning.solve({"code": issue.code, "message": issue.message}, {"block_id": issue.block_id, "source_text": block.source_text if block else None, "bbox": block.bbox if block else None})
                unresolved = bool(unresolved_issues)
            if current and not fitted:
                raise StudioError("reconstruction_failed","No translated blocks could be rendered. The generated PDF is a source-only draft, not a successful translation.",422)
            return "completed_with_issues" if unresolved else "completed"

    def refresh_geometry(self, document_id):
        """Upgrade digital geometry without discarding text revisions or calling models."""
        extracted = TextReaderAgent().run(self.storage.path(document_id,"source.pdf"))
        new_blocks = BlockMakerAgent().run(document_id,extracted)
        with self.sessions() as session:
            digital_pages = {p.number for p in session.scalars(select(Page).where(Page.document_id==document_id)) if p.metadata_json.get("extraction_method","digital")=="digital"}
            identities = set()
            for item in new_blocks:
                if item["page_number"] not in digital_pages:
                    continue
                identities.add(item["id"])
                block = session.get(Block,item["id"])
                if block:
                    changed = block.bbox!=item["bbox"] or block.style!=item["style"] or block.source_text!=item["source_text"]
                    for translation in session.scalars(select(Translation).where(Translation.block_id==block.id)):
                        if "source_hash" not in translation.metadata_json:
                            translation.metadata_json = {**translation.metadata_json,"source_hash":source_hash(block.source_text)}
                    for key,value in item.items():
                        setattr(block,key,value)
                else:
                    session.add(Block(**item))
                    changed = True
                if changed:
                    session.get(Page,f"{document_id}-p{item['page_number']}").dirty = True
            for block in session.scalars(select(Block).where(Block.document_id==document_id)):
                if block.page_number in digital_pages and block.id not in identities:
                    block.style = {**block.style,"retired":True}
                    for issue in session.scalars(select(Issue).where(Issue.block_id==block.id)):
                        issue.resolution="resolved"
            session.commit()
            fields=("id","document_id","page_number","type","reading_order","bbox","source_text","style","confidence")
            records=[{field:getattr(b,field) for field in fields} for b in session.scalars(select(Block).where(Block.document_id==document_id)) if not b.style.get("retired")]
            self.storage.write_json(document_id,"structure.json",{"pages":[p.metadata_json for p in session.scalars(select(Page).where(Page.document_id==document_id).order_by(Page.number))],"blocks":records})

    def source_previews(self, source, document_id):
        with fitz.open(source) as pdf:
            for page in pdf:
                page.get_pixmap().save(str(self.storage.path(document_id, f"source-{page.number+1}.png")))

    def render(self, document_id):
        self.refresh_geometry(document_id)
        with self.sessions() as session:
            document = session.get(Document, document_id)
            source = self.storage.path(document_id, "source.pdf")
            pages = list(session.scalars(select(Page).where(Page.document_id == document_id).order_by(Page.number)))
            for page in pages:
                if not page.dirty:
                    continue
                for issue in session.scalars(select(Issue).where(Issue.document_id == document_id, Issue.page_number == page.number, Issue.stage == "checker")):
                    issue.resolution = "resolved"
                blocks = [b for b in session.scalars(select(Block).where(Block.document_id == document_id, Block.page_number == page.number)) if not b.style.get("retired")]
                with fitz.open(source) as pdf:
                    graphics = [list(image["bbox"]) for image in pdf[page.number-1].get_image_info()]
                    graphics += [list(drawing["rect"]) for drawing in pdf[page.number-1].get_drawings()]
                entries = []
                occupied = [b.bbox for b in blocks] + graphics
                for block in blocks:
                    translation = latest(session, block.id, document.target_language, document.script)
                    if not translation:
                        stale=session.get(LayoutResult,block.id)
                        if stale:
                            session.delete(stale)
                        session.add(Issue(document_id=document_id, block_id=block.id, page_number=page.number, code="missing_translation", stage="checker", message="Source text retained because no translation is available"))
                        continue
                    result = self.reorganiser.run(block, translation, page, [r for r in occupied if r != block.bbox])
                    layout = session.get(LayoutResult, block.id)
                    if layout:
                        for key, value in result.items():
                            setattr(layout, key, value)
                    else:
                        layout = LayoutResult(block_id=block.id, **result)
                        session.add(layout)
                    occupied.append(result["bbox"])
                    entries.append((block, translation, layout))
                destination = self.storage.path(document_id, f"page-{page.number}.pdf")
                Renderer().render_page(source, page.number, entries, destination)
                for issue in CheckerAgent().run(source, destination, page.number, entries):
                    session.add(Issue(document_id=document_id, **issue))
                page.dirty = False
                session.commit()
            active=[b for b in session.scalars(select(Block).where(Block.document_id==document_id)) if not b.style.get("retired")]
            has_rendered=any(latest(session,b.id,document.target_language,document.script) and (l:=session.get(LayoutResult,b.id)) and l.fit_status=="fitted" for b in active)
            kind="final" if has_rendered or not active else "draft"
            destination = self.storage.path(document_id, kind+".pdf")
            Renderer().assemble(source, {p.number:self.storage.path(document_id,f"page-{p.number}.pdf") for p in pages}, destination)
            session.add(Artifact(document_id=document_id, kind=kind, path=str(destination)))
            session.commit()
