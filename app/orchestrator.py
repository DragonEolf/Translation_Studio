import asyncio
import json
from collections import Counter
from threading import RLock
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
from app.services.paragraph_flow import ParagraphFlow
from copy import deepcopy
from app.services.typography import TypographyPass, diagnostics
from app.config import StudioError
from app.services.cancellation import CancellationToken
from app.services import transliteration as translit
from app.services.ocr_overlaps import overlap_reviews
from app.agents.ocr_reader import OCRReaderAgent
from app.services.rich_text import group_blocks, export_xliff, rich, format_hash, source_runs, AUTO_PARAGRAPH_MIN_SPANS


_pdf_lock = RLock()


def _pdf_call(func, *args):
    with _pdf_lock:
        return func(*args)


async def pdf_thread(func, *args):
    return await asyncio.to_thread(_pdf_call, func, *args)


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
        self.ocr_provider = ocr_provider

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
                metadata = await pdf_thread(InspectorAgent().run, source, self.settings.studio_max_pages)
                extracted = await pdf_thread(TextReaderAgent().run, source)
                blocks = await pdf_thread(BlockMakerAgent().run, document.id, extracted)
                for issue in session.scalars(select(Issue).where(Issue.document_id==document.id,Issue.stage=="ocr")):
                    issue.resolution = "resolved"
                for item in metadata:
                    cancel.check()
                    number = item["number"]
                    if item["extraction_method"] == "ocr":
                        reader = OCRReaderAgent()
                        png, width, height = await pdf_thread(reader.image, source, number, self.settings.studio_ocr_dpi)
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
                        session.add(Issue(document_id=document.id,page_number=number,block_id=None,code="ocr_layout_estimated",severity="warning",stage="ocr",message="Scanned font styles are estimated; text regions use locally estimated paper backgrounds. Verify scan and transcription before final use."))
                    job.progress = .9*number/len(metadata)
                await pdf_thread(self.source_previews, source, document.id)
                # Restartable inspection replaces only incomplete extraction state.
                existing = set(session.scalars(select(Block.id).where(Block.document_id == document.id)))
                for item in metadata:
                    stored_page=session.get(Page, f"{document.id}-p{item['number']}")
                    if not stored_page:
                        session.add(Page(id=f"{document.id}-p{item['number']}", document_id=document.id, number=item["number"], width=item["width"], height=item["height"], metadata_json=item))
                    elif not self.settings.studio_cache_enabled:
                        stored_page.metadata_json=item
                        stored_page.dirty=True
                session.flush()  # Persist pages before blocks reference their page keys.
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
                structured = self.storage.write_json(document.id, "structure.json", {"pages": metadata, "blocks": blocks})
                session.add(Artifact(document_id=document.id, kind="structure", path=str(structured)))
                session.commit()
                return "completed_with_issues" if session.scalar(select(Issue.id).where(Issue.document_id==document.id,Issue.resolution!="resolved")) else "completed"
            if job.operation == "translate":
                for page in session.scalars(select(Page).where(Page.document_id==document.id)):
                    page.metadata_json={**page.metadata_json,'layout_mode':job.payload.get('layout_mode','spans')}
                session.commit()
                await pdf_thread(self.refresh_geometry,document.id)
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
                        diagnostics = metadata.get("translation_diagnostics", {})
                        if diagnostics.get("splits"):
                            stats["recovered_passages"] = stats.get("recovered_passages", 0) + 1
                            session.add(Issue(document_id=document.id,block_id=block.id,page_number=block.page_number,code="passage_translation_split",stage="translator",severity="warning",message="Incomplete translation recovered by translating smaller passages; review sentence flow and terminology."))
                    else:
                        session.add(Issue(document_id=document.id,block_id=block.id,page_number=block.page_number,code=error.code,stage="translator",message=error.message))
                        if getattr(error, "diagnostics", None):
                            stats.setdefault("failed_passages", {})[block.id] = error.diagnostics
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
            if job.operation == "translate" and job.payload.get("transliterate"):
                await self.automatic_transliteration(session, document, job, cancel)
            cancel.check()
            layout_report = await pdf_thread(self.render, document.id, job.id if job.operation=="translate" and not self.settings.studio_cache_enabled else None, cancel)
            cancel.check()
            with self.sessions() as check:
                current = [b for b in check.scalars(select(Block).where(Block.document_id==document.id)) if not b.style.get("retired")]
                fitted = sum(1 for b in current if latest(check,b.id,document.target_language,document.script) and (layout:=check.get(LayoutResult,b.id)) and layout.fit_status=="fitted")
                summary={"total_blocks":len(current),"rendered_blocks":fitted,"retained_source_blocks":len(current)-fitted}
                result_job=check.get(Job,job_id)
                result_job.payload={**result_job.payload,"result":summary,"layout_review_stats":layout_report}
                check.commit()
                unresolved_issues = list(check.scalars(select(Issue).where(Issue.document_id == document.id, Issue.resolution != "resolved")))
                unresolved = bool(unresolved_issues)
            if current and not fitted:
                raise StudioError("reconstruction_failed","No translated blocks could be rendered. The generated PDF is a source-only draft, not a successful translation.",422)
            return "completed_with_issues" if unresolved else "completed"

    async def automatic_transliteration(self, session, document, job, cancel):
        """Generate all replacements before saving any transliterated revision."""
        entries = [(b, latest(session, b.id, document.target_language, document.script))
                   for b in session.scalars(select(Block).where(Block.document_id == document.id))
                   if not b.style.get('retired')]
        entries = [(b, t) for b, t in entries if t and (
            self.settings.studio_cache_enabled or t.metadata_json.get('job_id') == job.id)]
        words = translit.candidates(entries)
        replacements = {}
        stats = {'candidate_words': len(words), 'calls': 0, 'changed_blocks': 0}
        for index, item in enumerate(words):
            cancel.check()
            word = item['word']
            body = await cancel.wait(self.transliterator.transliterate(
                translit.pronunciation_input(word), document.target_language))
            replacements[word] = translit.validate_replacement(body.get('transliteration'))
            stats['calls'] += 1
            job.progress = .7 + .1 * (index + 1) / max(1, len(words))
            job.payload = {**job.payload, 'transliteration_stats': dict(stats)}
            session.commit()
        cancel.check()
        for block, translation in entries:
            text, metadata = translit.replace(translation, replacements)
            if text == translation.text:
                continue
            previous = latest(session, block.id)
            session.add(Translation(block_id=block.id, target_language=document.target_language,
                script=document.script, text=text, revision=previous.revision + 1,
                metadata_json={**metadata, 'job_id': job.id, 'automatic_transliteration': True}))
            session.get(Page, f'{document.id}-p{block.page_number}').dirty = True
            stats['changed_blocks'] += 1
        job.payload = {**job.payload, 'transliteration_stats': stats}
        session.commit()

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
                    created = Block(**item)
                    session.add(created)
                    # Reuse revisions only when an old block describes exactly
                    # this span. Never attach a combined row/paragraph translation
                    # to one of its new child boxes.
                    single_source = item['style'].get('rich_text', {}).get('source_spans', [])
                    single_id = single_source[0]['id'] if len(single_source) == 1 else None
                    matching = [b for b in previous_blocks if
                        b.page_number == item["page_number"] and
                        b.source_text == item["source_text"] and
                        (b.id == single_id or (len(b.bbox) == 4 and
                        all(abs(a-c)<.01 for a,c in zip(b.bbox,item["bbox"]))))]
                    if len(matching) == 1:
                        for old in session.scalars(select(Translation).where(Translation.block_id==matching[0].id)):
                            if old.metadata_json.get("source_hash",source_hash(matching[0].source_text)) == source_hash(item["source_text"]):
                                metadata = {**old.metadata_json, "source_hash":source_hash(item["source_text"]), "migrated_from":old.block_id}
                                # A single source span needs no new sentence context.
                                # Wrap its retained translation in the known local style.
                                if single_id and not metadata.get('rich_text'):
                                    runs = source_runs(created)
                                    if len(runs) == 1:
                                        metadata = {**metadata, 'rich_text':{'runs':[{**runs[0], 'text':old.text}], 'fallback':False}, 'format_hash':format_hash(created)}
                                session.add(Translation(block_id=item["id"],target_language=old.target_language,script=old.script,text=old.text,revision=old.revision,metadata_json=metadata))
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
        layout_report={'typography':[], 'pages':[]}
        report_path = self.storage.path(document_id, 'layout-report.json')
        try:
            previous_report = json.loads(report_path.read_text())
        except (OSError, ValueError):
            previous_report = {}
        with self.sessions() as session:
            document = session.get(Document, document_id)
            source = self.storage.path(document_id, "source.pdf")
            pages = list(session.scalars(select(Page).where(Page.document_id == document_id).order_by(Page.number)))
            for page in pages:
                cancel.check()
                if self.settings.studio_cache_enabled and not page.dirty:
                    cached = next((p for p in previous_report.get('pages', []) if p['page'] == page.number), None)
                    if cached:
                        layout_report['pages'].append({**cached, 'cached':True})
                    continue
                for issue in session.scalars(select(Issue).where(Issue.document_id == document_id, Issue.page_number == page.number, Issue.stage == "checker")):
                    issue.resolution = "resolved"
                blocks = [b for b in session.scalars(select(Block).where(Block.document_id == document_id, Block.page_number == page.number)) if not b.style.get("retired")]
                overlap_flags = overlap_reviews(blocks)
                for block in blocks:
                    style = {k:v for k,v in block.style.items() if k != 'ocr_overlap_with'}
                    if block.id in overlap_flags:
                        style['ocr_overlap_with'] = overlap_flags[block.id]
                    if style != block.style:
                        block.style = style
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
                renderer = Renderer(self.reorganiser.fonts)
                checker = CheckerAgent()
                renderer.render_page(source, page.number, entries, destination)
                def review_layout():
                    issues = checker.run(source, destination, page.number, entries)
                    translated_ids = {b.id for b, _, _ in entries}
                    issues.extend({'block_id':b.id, 'page_number':page.number,
                        'code':'missing_translation', 'stage':'checker', 'severity':'error',
                        'message':'Source text retained because no translation is available'}
                        for b in blocks if b.id not in translated_ids)
                    if self.settings.studio_typography_enabled:
                        issues.extend({'block_id':b.id, 'page_number':page.number, 'code':code,
                            'message':message, 'stage':'checker', 'severity':'warning'}
                            for b, code, message in diagnostics(entries, self.settings.studio_typography_min_font_pt))
                    return issues
                before = review_layout()
                snapshots = {b.id: {key: deepcopy(getattr(l, key)) for key in
                    ('bbox', 'font', 'font_size', 'line_breaks', 'fit_status')} for b, _, l in entries}
                cell_changes = CellLayout(self.reorganiser.fonts, self.settings.studio_typography_min_font_pt).apply(entries, blocks, page, images)
                paragraph_changes = ParagraphFlow(self.reorganiser.fonts, self.settings.studio_typography_min_font_pt).apply(entries, blocks, page, graphics)
                adjusted = {'changed_blocks': [], 'refits': 0}
                if self.settings.studio_typography_enabled:
                    adjusted = TypographyPass(self.reorganiser.fonts,self.settings.studio_typography_min_font_pt,self.settings.studio_typography_max_refits).apply(entries,page)
                changes = cell_changes + paragraph_changes + adjusted['changed_blocks']
                checked = before
                accepted = False
                if changes:
                    renderer.render_page(source, page.number, entries, destination)
                    candidate_issues = review_layout()
                    # Reject any newly introduced per-block error, even if another
                    # region improved. Previously fitted translations must survive.
                    prior_errors = {(i['block_id'], i['code']) for i in before if i['severity'] == 'error'}
                    new_errors = {(i['block_id'], i['code']) for i in candidate_issues if i['severity'] == 'error'}
                    retained = all(l.fit_status == 'fitted' for b, _, l in entries
                                   if snapshots[b.id]['fit_status'] == 'fitted')
                    accepted = new_errors <= prior_errors and retained
                    if accepted:
                        checked = candidate_issues
                    else:
                        for block, _, layout in entries:
                            for key, value in snapshots[block.id].items():
                                setattr(layout, key, value)
                        renderer.render_page(source, page.number, entries, destination)
                layout_report['typography'].append({'page':page.number, **adjusted, 'accepted':accepted})
                failed_ids = {i['block_id'] for i in checked if i['code'] in {'missing_text', 'clipping', 'collision'}}
                block_outcomes = []
                for block, translation, layout in entries:
                    reason = None
                    if layout.fit_status == 'overflow':
                        reason = 'insufficient_cell_height' if block.style.get('table_cell_bbox') else 'minimum_font_reached'
                    elif layout.fit_status == 'unsupported_glyphs':
                        path = self.reorganiser.fonts.resolve(translation.target_language, translation.script, block.style.get('bold', False))
                        reason = 'unavailable_font' if path == 'missing' else 'missing_glyph'
                    elif block.id in failed_ids:
                        reason = 'rendering_mismatch'
                    elif layout.fit_status != 'fitted':
                        reason = layout.fit_status
                    block_outcomes.append({'block_id':block.id, 'translation_id':translation.id,
                        'translation_hash':source_hash(translation.text),
                        'translation_retained':layout.fit_status == 'fitted' and block.id not in failed_ids,
                        'fit_status':layout.fit_status, 'failure_reason':reason,
                        'font_size':layout.font_size, 'bbox':layout.bbox,
                        'available_height':layout.bbox[3]-layout.bbox[1],
                        'source_line_count':len(block.source_text.splitlines()),
                        'before':snapshots[block.id]})
                included = {b.id for b, _, _ in entries}
                block_outcomes.extend({'block_id':b.id, 'translation_id':None,
                    'translation_retained':False, 'failure_reason':'missing_translation'}
                    for b in blocks if b.id not in included)
                layout_report['pages'].append({'page':page.number,
                    'cell_changes':cell_changes, 'paragraph_changes':paragraph_changes,
                    'change_accepted':accepted, 'issues_before':len(before), 'issues_after':len(checked),
                    'issues_reduced':len(checked) < len(before),
                    'issue_counts_before':dict(Counter(i['code'] for i in before)),
                    'issue_counts_after':dict(Counter(i['code'] for i in checked)), 'blocks':block_outcomes})
                cancel.check()
                for issue in checked:
                    session.add(Issue(document_id=document_id, **issue))
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
