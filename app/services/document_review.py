"""Read-only review snapshots: PDF coordinates match the rotated page previews.

Automatic findings describe rendering checks, not semantic translation accuracy.
Human review decisions stay separate from these findings in the browser.
"""
import hashlib
import json

import pymupdf as fitz
from sqlalchemy import select

from app.config import StudioError
from app.models import Document, Page, Block, LayoutResult, Issue, Job
from app.orchestrator import latest


def review_snapshot(sessions, storage, document_id):
    """Return current regions and findings without changing jobs or PDF files."""
    with sessions() as session:
        doc = session.get(Document, document_id)
        if not doc:
            raise StudioError("not_found", "Resource not found", 404)
        if session.scalar(select(Job.id).where(Job.document_id == document_id,
                                             Job.status.in_(["queued", "running", "cancelling"]))):
            raise StudioError("document_busy", "Wait for the current job to finish before reviewing its output", 409)
        output = next((name for name in ("final.pdf", "draft.pdf")
                       if storage.path(document_id, name).exists()), None)
        if not output:
            raise StudioError("artifact_not_ready", "A translated PDF or draft is not available yet", 404)
        blocks = [b for b in session.scalars(select(Block).where(Block.document_id == document_id)
                  .order_by(Block.page_number, Block.reading_order)) if not b.style.get("retired")]
        block_ids = {b.id for b in blocks}
        findings = [i for i in session.scalars(select(Issue).where(
                    Issue.document_id == document_id, Issue.resolution != "resolved"))
                    if i.block_id is None or i.block_id in block_ids]
        by_block = {}
        unlocated = {}
        for issue in findings:
            entry = {key: getattr(issue, key) for key in ("code", "severity", "message", "stage")}
            if issue.block_id:
                by_block.setdefault(issue.block_id, []).append(entry)
            else:
                unlocated.setdefault(issue.page_number, []).append(entry)
        pages, regions = [], []
        with fitz.open(storage.path(document_id, "source.pdf")) as original:
            for page in session.scalars(select(Page).where(Page.document_id == document_id).order_by(Page.number)):
                pdf_page = original[page.number - 1]
                base = f"/v1/documents/{document_id}/artifacts/"
                pages.append({"number": page.number, "width": pdf_page.rect.width,
                              "height": pdf_page.rect.height,
                              "source_preview": base + f"source-{page.number}.png" if storage.path(document_id, f"source-{page.number}.png").exists() else None,
                              "translated_preview": base + f"page-{page.number}.png" if storage.path(document_id, f"page-{page.number}.png").exists() else None})

            def rectangles(number, boxes):
                page = original[number - 1]
                result = []
                for box in boxes:
                    rect = (fitz.Rect(box) * page.rotation_matrix) & page.rect
                    if not rect.is_empty and not rect.is_infinite:
                        result.append(list(rect))
                return result

            for block in blocks:
                translation = latest(session, block.id, doc.target_language, doc.script) if doc.target_language else None
                layout = session.get(LayoutResult, block.id)
                rendered = bool(translation and layout and layout.fit_status == "fitted")
                issues = by_block.get(block.id, [])
                status = "change" if not rendered or any(i["severity"] == "error" for i in issues) else "review" if issues else "clear"
                regions.append({"id": block.id, "page_number": block.page_number,
                                "source_rects": rectangles(block.page_number, block.style.get("source_regions") or [block.bbox]),
                                "translated_rects": rectangles(block.page_number, [layout.bbox] if rendered else block.style.get("source_regions") or [block.bbox]),
                                "source_text": block.source_text,
                                "translated_text": translation.text if translation else None,
                                "rendered": rendered, "status": status, "issues": issues})
        for number, issues in unlocated.items():
            regions.append({"id": f"page-{number}-findings", "page_number": number,
                            "source_rects": [], "translated_rects": [],
                            "source_text": "", "translated_text": None, "rendered": None,
                            "status": "change" if any(i["severity"] == "error" for i in issues) else "review",
                            "issues": issues})
        stat = storage.path(document_id, output).stat()
        version = hashlib.sha256(json.dumps([output, stat.st_mtime_ns, stat.st_size, regions], sort_keys=True).encode()).hexdigest()[:20]
        return {"document_id": document_id, "filename": doc.filename, "output": output,
                "version": version, "pages": pages, "regions": regions,
                "previous_available": storage.path(document_id,"previous.pdf").exists(),
                "regroup_recommended": any(p.metadata_json.get('layout_mode') in {'auto','paragraphs'}
                    and p.metadata_json.get('extraction_method','digital')=='digital'
                    and p.metadata_json.get('layout_policy_version',0)<1
                    for p in session.scalars(select(Page).where(Page.document_id==document_id)))}
