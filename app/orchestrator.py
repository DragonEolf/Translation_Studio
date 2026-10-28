import asyncio
import pymupdf as fitz
from sqlalchemy import select
from app.models import Document, Page, Block, Translation, LayoutResult, Issue, Job, Artifact
from app.agents.inspector import InspectorAgent
from app.agents.text_reader import TextReaderAgent
from app.agents.block_maker import BlockMakerAgent
from app.agents.translator import TranslatorAgent, source_hash, literal_only
from app.agents.reorganiser import ReorganiserAgent
from app.agents.checker import CheckerAgent
from app.services.renderer import Renderer
from app.services.cell_layout import CellLayout
from app.services.typography import TypographyPass, diagnostics
from app.services.vision_repair import review_page
from app.config import StudioError
from app.services.cancellation import CancellationToken
from app.services import transliteration as translit
from app.agents.ocr_reader import OCRReaderAgent
from app.services.rich_text import group_blocks, export_xliff, rich, format_hash, AUTO_PARAGRAPH_MIN_SPANS


def latest(session, block_id, language=None, script=None):
    query = select(Translation).where(Translation.block_id == block_id)
    if language:
        query = query.where(Translation.target_language == language, Translation.script == script)
    block = session.get(Block,block_id)
    for translation in session.scalars(query.order_by(Translation.revision.desc())):
        if language and rich(block) and translation.metadata_json.get('format_hash')!=format_hash(block):
            continue
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
        self.vision_reviewer = None

    async def run(self, job_id, cancel=None):
        cancel = cancel or CancellationToken()
        cancel.check()
        with self.sessions() as session:
            job = session.get(Job, job_id)
            document = session.get(Document, job.document_id)
            source = self.storage.path(document.id, "source.pdf")
            if job.operation == "transliterate_preview":
                suggestions=[]
                words=job.payload['words']
                for index,word in enumerate(words):
                    cancel.check()
                    body=await cancel.wait(self.transliterator.transliterate(translit.pronunciation_input(word),job.payload['target_language']))
                    replacement=translit.validate_replacement(body.get('transliteration'))
                    suggestions.append({'word':word,'replacement':replacement})
                    job.progress=(index+1)/len(words)
                    job.payload={**job.payload,'suggestions':list(suggestions)}
                    session.commit()
                return 'completed'
            if job.operation == "inspect":
                metadata = await asyncio.to_thread(InspectorAgent().run, source, self.settings.studio_max_pages)
                extracted = await asyncio.to_thread(TextReaderAgent().run, source)
                blocks = await asyncio.to_thread(BlockMakerAgent().run, document.id, extracted)
                for issue in session.scalars(select(Issue).where(Issue.document_id==document.id,Issue.stage=="ocr")):
                    issue.resolution = "resolved"
                for item in metadata:
                    cancel.check()
                    number = item["number"]
                    if item["extraction_method"] == "ocr":
                        reader = OCRReaderAgent()
                        png, width, height = await asyncio.to_thread(reader.image, source, number, self.settings.studio_ocr_dpi)
                        cache = self.storage.path(document.id,f"ocr-{number}-{self.settings.studio_ocr_dpi}.json")
                        if self.settings.studio_cache_enabled and cache.exists():
                            import json
                            response = json.loads(cache.read_text(encoding="utf-8"))
                        else:
                            response = await cancel.wait(self.ocr_provider.extract(png))
                            reader.blocks(document.id,number,item,response,width,height)
                            if self.settings.studio_cache_enabled:
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
                    stored_page=session.get(Page, f"{document.id}-p{item['number']}")
                    if not stored_page:
                        session.add(Page(id=f"{document.id}-p{item['number']}", document_id=document.id, number=item["number"], width=item["width"], height=item["height"], metadata_json=item))
                    elif not self.settings.studio_cache_enabled:
                        stored_page.metadata_json=item
                        stored_page.dirty=True
                for item in blocks:
                    if item["id"] not in existing:
                        session.add(Block(**item))
                    elif not self.settings.studio_cache_enabled:
                        stored_block=session.get(Block,item["id"])
                        for key,value in item.items():
                            setattr(stored_block,key,value)
                if not self.settings.studio_cache_enabled:
                    current_ids={item["id"] for item in blocks}
                    for identity in existing-current_ids:
                        retired=session.get(Block,identity)
                        retired.style={**retired.style,"retired":True}
                document.page_count = len(metadata)
                await asyncio.to_thread(self.source_previews, source, document.id)
                structured = self.storage.write_json(document.id, "structure.json", {"pages": metadata, "blocks": blocks})
                session.add(Artifact(document_id=document.id, kind="structure", path=str(structured)))
                session.commit()
                return "completed_with_issues" if session.scalar(select(Issue.id).where(Issue.document_id==document.id,Issue.resolution!="resolved")) else "completed"
            if job.operation == "translate":
                for page in session.scalars(select(Page).where(Page.document_id==document.id)):
                    page.metadata_json={**page.metadata_json,'layout_mode':job.payload.get('layout_mode','spans')}
                session.commit()
                await asyncio.to_thread(self.refresh_geometry,document.id)
                session.expire_all()
                changed_target = (document.target_language, document.script) != (job.payload["target_language"], job.payload["script"])
                if changed_target:
                    for issue in session.scalars(select(Issue).where(Issue.document_id == document.id, Issue.stage == "translator")):
                        issue.resolution = "resolved"
                document.target_language, document.script = job.payload["target_language"], job.payload["script"]
                blocks = [b for b in session.scalars(select(Block).where(Block.document_id == document.id).order_by(Block.page_number, Block.reading_order)) if not b.style.get("retired")]
                session.commit()
                stats={"batch_calls":0,"invalid_batches":0,"individual_calls":0,"batch_tokens":0}
                completed=0
                pending=[]

                def save_result(block,text,metadata,error=None):
                    nonlocal completed
                    cancel.check()
                    if error is None:
                        previous=latest(session,block.id)
                        session.add(Translation(block_id=block.id,target_language=document.target_language,script=document.script,text=text,revision=previous.revision+1 if previous else 1,metadata_json={**metadata,"job_id":job.id,**({"format_hash":format_hash(block)} if rich(block) else {})}))
                        for issue in session.scalars(select(Issue).where(Issue.block_id==block.id,Issue.stage=="translator")):
                            issue.resolution="resolved"
                        if metadata.get('rich_text',{}).get('fallback'):
                            session.add(Issue(document_id=document.id,block_id=block.id,page_number=block.page_number,code='inline_formatting_fallback',stage='translator',severity='warning',message='Inline tags failed validation. Formatted runs were translated separately; review sentence flow.'))
                    else:
                        session.add(Issue(document_id=document.id,block_id=block.id,page_number=block.page_number,code=error.code,stage="translator",message=error.message))
                    session.get(Page,f"{document.id}-p{block.page_number}").dirty=True
                    completed+=1
                    job.progress=.7*completed/max(1,len(blocks))
                    job.payload={**job.payload,"translation_batch_stats":dict(stats)}
                    session.commit()

                for block in blocks:
                    if changed_target:
                        session.get(Page,f"{document.id}-p{block.page_number}").dirty=True
                    if self.settings.studio_cache_enabled and latest(session,block.id,document.target_language,document.script):
                        completed+=1
                    elif literal_only(block.source_text):
                        text,metadata=await self.translator.run(block,document.target_language,document.script)
                        save_result(block,text,metadata)
                    else:
                        pending.append(block)
                session.commit()
                for batch in self.translator.batches(pending,self.settings.studio_translation_batch_size,self.settings.studio_translation_batch_chars):
                    cancel.check()
                    stream = self.translator.translate_batch(batch,document.target_language,document.script,stats)
                    try:
                        while True:
                            try:
                                block,text,metadata,error = await cancel.wait(anext(stream))
                            except StopAsyncIteration:
                                break
                            save_result(block,text,metadata,error)
                    finally:
                        await stream.aclose()
            cancel.check()
            layout_report = await asyncio.to_thread(self.render, document.id, job.id if job.operation=="translate" and not self.settings.studio_cache_enabled else None, cancel)
            cancel.check()
            with self.sessions() as check:
                current = [b for b in check.scalars(select(Block).where(Block.document_id==document.id)) if not b.style.get("retired")]
                fitted = sum(1 for b in current if latest(check,b.id,document.target_language,document.script) and (layout:=check.get(LayoutResult,b.id)) and layout.fit_status=="fitted")
                summary={"total_blocks":len(current),"rendered_blocks":fitted,"retained_source_blocks":len(current)-fitted}
                result_job=check.get(Job,job_id)
                result_job.payload={**result_job.payload,"result":summary,"layout_review_stats":layout_report}
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
            stored_pages=list(session.scalars(select(Page).where(Page.document_id==document_id)))
            mode_pages={mode:{p.number for p in stored_pages if p.metadata_json.get('layout_mode')==mode} for mode in ('paragraphs','auto')}
            selected=[]
            for mode, numbers in mode_pages.items():
                if numbers:
                    grouped=group_blocks(new_blocks,extracted,min_spans=AUTO_PARAGRAPH_MIN_SPANS if mode=='auto' else 1)
                    selected.extend(b for b in grouped if b['page_number'] in numbers)
            grouped_pages=mode_pages['paragraphs'] | mode_pages['auto']
            new_blocks=[b for b in new_blocks if b['page_number'] not in grouped_pages]+selected
            digital_pages = {p.number for p in session.scalars(select(Page).where(Page.document_id==document_id)) if p.metadata_json.get("extraction_method","digital")=="digital"}
            previous_blocks = list(session.scalars(select(Block).where(Block.document_id==document_id)))
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
                    # Reuse revisions only when an old block describes exactly
                    # this span. Never attach a combined row/paragraph translation
                    # to one of its new child boxes.
                    matching = [b for b in previous_blocks if
                        b.page_number == item["page_number"] and
                        b.source_text == item["source_text"] and
                        len(b.bbox) == 4 and
                        all(abs(a-c)<.01 for a,c in zip(b.bbox,item["bbox"]))]
                    if len(matching) == 1:
                        for old in session.scalars(select(Translation).where(Translation.block_id==matching[0].id)):
                            if old.metadata_json.get("source_hash",source_hash(matching[0].source_text)) == source_hash(item["source_text"]):
                                session.add(Translation(block_id=item["id"],target_language=old.target_language,script=old.script,text=old.text,revision=old.revision,metadata_json={**old.metadata_json,"source_hash":source_hash(item["source_text"]),"migrated_from":old.block_id}))
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

    def render(self, document_id, translation_job_id=None, cancel=None):
        cancel = cancel or CancellationToken()
        cancel.check()
        self.refresh_geometry(document_id)
        layout_report={'typography':[], 'vision':{'calls':0,'accepted':0,'reviews':[],'usage':{}}}
        with self.sessions() as session:
            document = session.get(Document, document_id)
            source = self.storage.path(document_id, "source.pdf")
            pages = list(session.scalars(select(Page).where(Page.document_id == document_id).order_by(Page.number)))
            for page in pages:
                cancel.check()
                if self.settings.studio_cache_enabled and not page.dirty:
                    continue
                for issue in session.scalars(select(Issue).where(Issue.document_id == document_id, Issue.page_number == page.number, Issue.stage == "checker")):
                    issue.resolution = "resolved"
                blocks = [b for b in session.scalars(select(Block).where(Block.document_id == document_id, Block.page_number == page.number)) if not b.style.get("retired")]
                with fitz.open(source) as pdf:
                    images = [list(image["bbox"]) for image in pdf[page.number-1].get_image_info()]
                    graphics = list(images)
                    graphics += [list(drawing["rect"]) for drawing in pdf[page.number-1].get_drawings()]
                entries = []
                occupied = [b.bbox for b in blocks] + graphics
                for block in blocks:
                    cancel.check()
                    translation = latest(session, block.id, document.target_language, document.script)
                    if translation and translation_job_id and translation.metadata_json.get("job_id")!=translation_job_id:
                        translation=None
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
                CellLayout(self.reorganiser.fonts).apply(entries, blocks, page, images)
                if self.settings.studio_typography_enabled:
                    adjusted=TypographyPass(self.reorganiser.fonts,self.settings.studio_typography_min_font_pt,self.settings.studio_typography_max_refits).apply(entries,page)
                    layout_report['typography'].append({'page':page.number,**adjusted})
                destination = self.storage.path(document_id, f"page-{page.number}.pdf")
                Renderer(self.reorganiser.fonts).render_page(source, page.number, entries, destination)
                checked=CheckerAgent().run(source, destination, page.number, entries)
                cancel.check()
                if self.vision_reviewer is not None:
                    was_unavailable=layout_report['vision'].get('unavailable',False)
                    checked=review_page(self.vision_reviewer,self.settings,source,destination,page,entries,blocks,graphics,checked,self.reorganiser.fonts,layout_report['vision'])
                    if layout_report['vision'].get('unavailable') and not was_unavailable:
                        checked.append({'code':'vision_review_unavailable','message':'Optional vision review was unavailable; local rendering and checks were retained.','page_number':page.number,'block_id':None,'stage':'checker','severity':'warning'})
                for issue in checked:
                    session.add(Issue(document_id=document_id, **issue))
                if self.settings.studio_typography_enabled:
                    for block,code,message in diagnostics(entries,self.settings.studio_typography_min_font_pt):
                        session.add(Issue(document_id=document_id,block_id=block.id,page_number=page.number,code=code,message=message,stage='checker',severity='warning'))
                cancel.check()
                page.dirty = False
                session.commit()
            cancel.check()
            interchange=[]
            for block in session.scalars(select(Block).where(Block.document_id==document_id)):
                if block.style.get('retired') or not rich(block):
                    continue
                translation=latest(session,block.id,document.target_language,document.script)
                if translation and (not translation_job_id or translation.metadata_json.get('job_id')==translation_job_id):
                    interchange.append((block,translation))
            xliff=self.storage.path(document_id,'translation.xlf')
            if interchange:
                xliff.write_bytes(export_xliff(interchange,document.target_language))
            else:
                xliff.unlink(missing_ok=True)
            active=[b for b in session.scalars(select(Block).where(Block.document_id==document_id)) if not b.style.get("retired")]
            has_rendered=any(latest(session,b.id,document.target_language,document.script) and (l:=session.get(LayoutResult,b.id)) and l.fit_status=="fitted" for b in active)
            kind="final" if has_rendered or not active else "draft"
            if kind=="draft":
                self.storage.path(document_id,"final.pdf").unlink(missing_ok=True)
            destination = self.storage.path(document_id, kind+".pdf")
            cancel.check()
            Renderer().assemble(source, {p.number:self.storage.path(document_id,f"page-{p.number}.pdf") for p in pages}, destination)
            session.add(Artifact(document_id=document_id, kind=kind, path=str(destination)))
            session.commit()
        self.storage.write_json(document_id,'layout-report.json',layout_report)
        return layout_report
