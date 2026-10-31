"""Selected-region repair: stage candidates, check them, then publish with backup."""

import json
import shutil
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as N

import pymupdf as fitz
from sqlalchemy import select

from app.agents.checker import CheckerAgent
from app.agents.translator import source_hash, validate_literals
from app.config import StudioError
from app.models import Block, Issue, LayoutResult, Page, Translation
from app.services.layout_tools import fits, minimum_body_size
from app.services.renderer import Renderer
from app.services.rich_text import export_xliff, literal_source, literal_target, rich

HARD = {
    "overflow",
    "collision",
    "clipping",
    "missing_text",
    "protected_value_changed",
    "graphics_changed",
    "page_dimensions_changed",
    "unsupported_glyphs",
}


class RepairRejected(ValueError):
    def __init__(self, issues):
        super().__init__("Candidate failed rendering checks")
        self.issues = issues


def detached(block, translation, layout):
    return (
        N(
            id=block.id,
            bbox=list(block.bbox),
            source_text=block.source_text,
            style=deepcopy(block.style),
            confidence=block.confidence,
            type=block.type,
        ),
        N(
            text=translation.text,
            target_language=translation.target_language,
            script=translation.script,
            metadata_json=deepcopy(translation.metadata_json),
        ),
        N(
            **{
                key: deepcopy(getattr(layout, key))
                for key in ("bbox", "font", "font_size", "fit_status", "line_breaks")
            }
        ),
    )


def prepare(source, current, page, entry, issues, instruction, entries=(), blocks=()):
    block, translation, layout = entry
    region = (fitz.Rect(block.bbox) | fitz.Rect(layout.bbox)) + (-48, -48, 48, 48)
    if block.style.get("table_cell_bbox"):
        region |= fitz.Rect(block.style["table_cell_bbox"])
    region &= fitz.Rect(0, 0, page.width, page.height)
    zoom = min(2.0, 1200 / max(region.width, region.height))
    with fitz.open(source) as src, fitz.open(current) as dst:
        source_page = src[page.number - 1]
        original = source_page.get_pixmap(
            matrix=fitz.Matrix(zoom, zoom), clip=region
        ).tobytes("png")
        output = (
            dst[0]
            .get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=region)
            .tobytes("png")
        )
        graphics = []
        for item in source_page.get_image_info():
            box = fitz.Rect(item["bbox"])
            if region.intersects(box):
                canvas = bool(
                    block.style.get("ocr") and box.contains(fitz.Rect(layout.bbox))
                )
                graphics.append(
                    {
                        "kind": "image",
                        "bbox": list(box),
                        "role": "scan_canvas" if canvas else "obstacle",
                        "read_only": True,
                    }
                )
        for item in source_page.get_drawings():
            box = fitz.Rect(item["rect"])
            if region.intersects(box + (-0.5, -0.5, 0.5, 0.5)):
                graphics.append(
                    {
                        "kind": "vector_graphic",
                        "bbox": list(box),
                        "role": "protected_graphic",
                        "read_only": True,
                    }
                )
        graphics.sort(
            key=lambda n: sum(
                (a - b) ** 2 for a, b in zip(n["bbox"][:2], layout.bbox[:2])
            )
        )
    context = {
        "block_id": block.id,
        "source_text": block.source_text,
        "current_translation": translation.text,
        "target_language": translation.target_language,
        "target_script": translation.script,
        "bbox": layout.bbox,
        "source_bbox": block.bbox,
        "cell_bbox": block.style.get("table_cell_bbox"),
        "font_size": layout.font_size,
        "minimum_font_size": min(6, block.style["size"]),
        "page_size": [page.width, page.height],
        "crop_bbox": list(region),
        "issues": issues,
        "reviewer_note": instruction,
        "styled_runs": translation.metadata_json.get("rich_text", {}).get("runs")
        if rich(block)
        else None,
    }
    by_id = {b.id: (t, entry_layout) for b, t, entry_layout in entries}
    neighbours = []
    for other in blocks:
        if other.id == block.id:
            continue
        t, entry_layout = by_id.get(other.id, (None, None))
        box = entry_layout.bbox if entry_layout else other.bbox
        if not region.intersects(fitz.Rect(box)) and not region.intersects(
            fitz.Rect(other.bbox)
        ):
            continue
        neighbours.append(
            {
                "block_id": other.id,
                "source_text": other.source_text[:2000],
                "kind": other.type,
                "role": "obstacle",
                "read_only": True,
                "current_translation": t.text[:2000] if t else None,
                "source_bbox": list(other.bbox),
                "bbox": list(box),
                "font_size": entry_layout.font_size
                if entry_layout
                else other.style.get("size"),
                "cell_bbox": other.style.get("table_cell_bbox"),
            }
        )
    neighbours.sort(
        key=lambda n: sum((a - b) ** 2 for a, b in zip(n["bbox"][:2], layout.bbox[:2]))
    )
    context["neighbouring_blocks"] = neighbours[:12]
    context["neighbouring_blocks_total"] = len(neighbours)
    context["nearby_graphics"] = graphics[:32]
    context["nearby_graphics_total"] = len(graphics)
    return context, original, output


def propose(entry, suggestion, entries, blocks, page, fonts, floor):
    block, translation, layout = deepcopy(entry)
    if suggestion.block_id != block.id or suggestion.action != "repair":
        raise ValueError("No repair proposed")
    if (
        block.style.get("vertical")
        or block.style.get("unsupported_ocr")
        or block.style.get("complex_table")
    ):
        raise ValueError("This region requires manual reconstruction")
    if suggestion.text is not None or suggestion.run_texts is not None:
        if rich(block):
            runs = translation.metadata_json.get("rich_text", {}).get("runs")
            if (
                suggestion.text is not None
                or not runs
                or suggestion.run_texts is None
                or len(suggestion.run_texts) != len(runs)
            ):
                raise ValueError("Repair must preserve every formatted run")
            for run, text in zip(runs, suggestion.run_texts):
                if not text.strip() or len(text) > 50000:
                    raise ValueError("Invalid styled replacement")
                validate_literals(run["text"], text)
                run["text"] = text
            translation.text = "".join(r["text"] for r in runs)
            validate_literals(literal_source(block), literal_target(runs))
        else:
            if suggestion.text is None or suggestion.run_texts is not None:
                raise ValueError("Invalid plain-text replacement")
            translation.text = suggestion.text
            validate_literals(block.source_text, translation.text)
        if len(translation.text) > 50000:
            raise ValueError("Replacement too long")
    if suggestion.bbox is not None:
        layout.bbox = suggestion.bbox
    if suggestion.font_size is not None:
        layout.font_size = suggestion.font_size
    if suggestion.align is not None:
        block.style["align"] = suggestion.align
    box, old = fitz.Rect(layout.bbox), fitz.Rect(entry[2].bbox)
    if box.is_empty or not fitz.Rect(0, 0, page.width, page.height).contains(box):
        raise ValueError("Repair exceeds page boundaries")
    cell = block.style.get("table_cell_bbox")
    if cell and not fitz.Rect(cell).contains(box):
        raise ValueError("Repair exceeds table cell")
    if (
        any(abs(a - b) > 24 for a, b in zip(layout.bbox, entry[2].bbox))
        or box.get_area() > old.get_area() * 1.5
    ):
        raise ValueError("Geometry change exceeds local repair limits")
    minimum = min(
        max(floor, 8) if block.style.get("ocr") else floor, block.style["size"]
    )
    if (
        not minimum <= layout.font_size <= block.style["size"]
        or minimum_body_size(block, translation, layout.font_size) < minimum - 0.01
    ):
        raise ValueError("Repair makes text too small or too large")
    by_id = {b.id: entry_layout for b, _, entry_layout in entries}
    obstacles = [
        fitz.Rect(by_id[b.id].bbox if b.id in by_id else b.bbox)
        for b in blocks
        if b.id != block.id
    ]
    with fitz.open(page.source) as pdf:
        obstacles += [
            fitz.Rect(i["bbox"]) for i in pdf[page.number - 1].get_image_info()
        ]
    # OCR's containing scan image is the canvas, not a collision obstacle.
    if block.style.get("ocr"):
        obstacles = [r for r in obstacles if not r.contains(old)]
    if any(
        (box & r).get_area() > max(1, (old & r).get_area()) + 0.01 for r in obstacles
    ):
        raise ValueError("Repair overlaps neighbouring content")
    path = None if layout.font == "sans-serif" else layout.font
    runs = (
        translation.metadata_json.get("rich_text", {}).get("runs")
        if rich(block)
        else None
    )
    for run in runs or [{"text": translation.text, "style": block.style}]:
        selected = (
            fonts.resolve(
                translation.target_language,
                translation.script,
                run["style"].get("bold", False),
            )
            if runs
            else path
        )
        if not fonts.supports(run["text"], selected, run["style"].get("bold", False)):
            raise ValueError("Replacement contains unsupported characters")
    layout.fit_status = "fitted"
    layout.line_breaks = translation.text.splitlines()
    if not fits(block, translation, layout, fonts, page):
        raise ValueError("Replacement does not fit")
    if (
        block.style,
        translation.text,
        layout.bbox,
        layout.font_size,
        layout.fit_status,
    ) == (
        entry[0].style,
        entry[1].text,
        entry[2].bbox,
        entry[2].font_size,
        entry[2].fit_status,
    ):
        raise ValueError("Proposal makes no change")
    return block, translation, layout


def validate_candidate(source, current, candidate, page, entries, replacement, fonts):
    checker = CheckerAgent(fonts)
    before = checker.run(source, current, page.number, entries)
    proposed = [
        replacement if b.id == replacement[0].id else (b, t, entry_layout)
        for b, t, entry_layout in entries
    ]
    Renderer(fonts).render_page(source, page.number, proposed, candidate)
    after = checker.run(source, candidate, page.number, proposed)
    before_errors = {
        (i["block_id"], i["code"]) for i in before if i["severity"] == "error"
    }
    after_errors = {
        (i["block_id"], i["code"]) for i in after if i["severity"] == "error"
    }
    if not after_errors <= before_errors or any(
        i["block_id"] == replacement[0].id and i["code"] in HARD for i in after
    ):
        failures = [
            i
            for i in after
            if (
                i["severity"] == "error"
                and (i["block_id"], i["code"]) not in before_errors
            )
            or (i["block_id"] == replacement[0].id and i["code"] in HARD)
        ]
        raise RepairRejected(failures)
    candidate.replace(current)
    candidate.with_suffix(".png").replace(current.with_suffix(".png"))
    return proposed, after


async def repair_document(orchestrator, session, document, job, cancel):
    from app.orchestrator import latest, pdf_thread

    storage, fonts = orchestrator.storage, orchestrator.reorganiser.fonts
    if job.payload.get("repair_published"):
        return "completed_with_issues"
    root = storage.path(document.id, "source.pdf").parent
    source = root / "source.pdf"
    original_output = root / job.payload["output"]
    records, accepted, pages = [], {}, {}
    with TemporaryDirectory(prefix="repair-", dir=root) as directory:
        stage = Path(directory)
        for identity in job.payload["region_ids"]:
            cancel.check()
            block = session.get(Block, identity)
            if (
                not block
                or block.document_id != document.id
                or block.style.get("retired")
            ):
                records.append(
                    {
                        "block_id": identity,
                        "status": "manual_review",
                        "reason": "Page/document findings need a specific text region selected.",
                    }
                )
                continue
            translation = latest(
                session, identity, document.target_language, document.script
            )
            layout = session.get(LayoutResult, identity)
            if not translation or not layout:
                records.append(
                    {
                        "block_id": identity,
                        "status": "manual_review",
                        "reason": "No translation or layout is available for this region.",
                    }
                )
                continue
            if block.page_number not in pages:
                page = session.get(Page, f"{document.id}-p{block.page_number}")
                blocks = list(
                    session.scalars(
                        select(Block).where(
                            Block.document_id == document.id,
                            Block.page_number == page.number,
                        )
                    )
                )
                blocks = [b for b in blocks if not b.style.get("retired")]
                entries = []
                for b in blocks:
                    t = latest(session, b.id, document.target_language, document.script)
                    entry_layout = session.get(LayoutResult, b.id)
                    if t and entry_layout:
                        entries.append(detached(b, t, entry_layout))
                current = stage / f"page-{page.number}.pdf"

                def extract_page():
                    with fitz.open(original_output) as src, fitz.open() as dst:
                        dst.insert_pdf(
                            src, from_page=page.number - 1, to_page=page.number - 1
                        )
                        dst.save(current)

                await pdf_thread(extract_page)
                pages[page.number] = {
                    "page": N(
                        number=page.number,
                        width=page.width,
                        height=page.height,
                        source=source,
                    ),
                    "blocks": blocks,
                    "entries": entries,
                    "current": current,
                    "issues": None,
                }
            state = pages[block.page_number]
            entry = next(e for e in state["entries"] if e[0].id == identity)
            findings = [
                {"code": i.code, "message": i.message}
                for i in session.scalars(
                    select(Issue).where(
                        Issue.block_id == identity, Issue.resolution != "resolved"
                    )
                )
            ]
            record = {"block_id": identity, "status": "manual_review"}
            try:
                context, a, b = await pdf_thread(
                    prepare,
                    source,
                    state["current"],
                    state["page"],
                    entry,
                    findings,
                    job.payload["instruction"],
                    state["entries"],
                    state["blocks"],
                )
                context["minimum_font_size"] = min(
                    max(orchestrator.settings.studio_typography_min_font_pt, 8)
                    if block.style.get("ocr")
                    else orchestrator.settings.studio_typography_min_font_pt,
                    block.style["size"],
                )
                context.update(
                    maximum_font_size=block.style["size"],
                    maximum_box_coordinate_change_pt=24,
                    maximum_box_area_ratio=1.5,
                )
                record["attempts"] = []
                for attempt in range(2):
                    cancel.check()
                    suggestion = await cancel.wait(
                        orchestrator.repair_provider.suggest(context, a, b)
                    )
                    record["reason"] = suggestion.reason
                    if suggestion.action != "repair":
                        record["status"] = suggestion.action
                        record["attempts"].append(
                            {"status": suggestion.action, "reason": suggestion.reason}
                        )
                        break
                    try:
                        replacement = await pdf_thread(
                            propose,
                            entry,
                            suggestion,
                            state["entries"],
                            state["blocks"],
                            state["page"],
                            fonts,
                            orchestrator.settings.studio_typography_min_font_pt,
                        )
                        proposed, checked = await pdf_thread(
                            validate_candidate,
                            source,
                            state["current"],
                            stage / "candidate.pdf",
                            state["page"],
                            state["entries"],
                            replacement,
                            fonts,
                        )
                    except (ValueError, RuntimeError, StudioError) as exc:
                        reason = (
                            exc.message
                            if isinstance(exc, StudioError)
                            else str(exc)[:200]
                        )
                        record["attempts"].append(
                            {"status": "rejected", "reason": reason}
                        )
                        if attempt == 1:
                            raise
                        # Retry against the unchanged accepted page, never the rejected candidate.
                        context = {
                            **context,
                            "retry_feedback": {
                                "previous_proposal": suggestion.model_dump(),
                                "rejection_reason": reason,
                                "rendering_issues": getattr(exc, "issues", [])[:12],
                            },
                        }
                        continue
                    state["entries"], state["issues"] = proposed, checked
                    accepted[identity] = replacement
                    record["status"] = "repaired"
                    record["attempts"].append(
                        {"status": "repaired", "reason": suggestion.reason}
                    )
                    break
            except (ValueError, RuntimeError, StudioError) as exc:
                record.update(
                    status="rejected",
                    reason=exc.message
                    if isinstance(exc, StudioError)
                    else str(exc)[:200],
                )
                if (
                    isinstance(exc, StudioError)
                    and exc.code == "repair_provider_unavailable"
                ):
                    for earlier in records:
                        if earlier["status"] == "repaired":
                            earlier.update(
                                status="rejected",
                                reason="Batch was not published because the AI service became unavailable.",
                            )
                    records.append(record)
                    # Do not publish a partial batch when the provider fails.
                    job.payload = {
                        **job.payload,
                        "repair_results": records,
                        "accepted": 0,
                    }
                    session.commit()
                    raise
            records.append(record)
            job.progress = len(records) / len(job.payload["region_ids"]) * 0.9
            # Only job progress is committed before publishing. All candidates
            # are detached objects and remain isolated from stored revisions.
            job.payload = {**job.payload, "repair_results": list(records)}
            session.commit()
        cancel.check()
        report = {
            "results": records,
            "accepted": len(accepted),
            "requested": len(job.payload["region_ids"]),
        }
        if accepted:
            changed_pages = {session.get(Block, key).page_number for key in accepted}
            await pdf_thread(
                Renderer(fonts).assemble,
                original_output,
                {n: pages[n]["current"] for n in changed_pages},
                stage / "final.pdf",
            )
            (stage / "repair-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            shutil.copy2(original_output, stage / "previous.pdf")
            publications = [
                (stage / name, root / name)
                for name in ["final.pdf", "previous.pdf", "repair-report.json"]
            ]
            for number in changed_pages:
                state = pages[number]
                publications += [
                    (state["current"], root / state["current"].name),
                    (
                        state["current"].with_suffix(".png"),
                        root / state["current"].with_suffix(".png").name,
                    ),
                ]
                for issue in session.scalars(
                    select(Issue).where(
                        Issue.document_id == document.id,
                        Issue.page_number == number,
                        Issue.stage == "checker",
                    )
                ):
                    issue.resolution = "resolved"
                for issue in state["issues"]:
                    session.add(Issue(document_id=document.id, **issue))
                session.get(Page, f"{document.id}-p{number}").dirty = False
            for identity, (b, t, entry_layout) in accepted.items():
                stored = session.get(Block, identity)
                stored.style = b.style
                old = latest(session, identity)
                session.add(
                    Translation(
                        block_id=identity,
                        target_language=t.target_language,
                        script=t.script,
                        text=t.text,
                        revision=old.revision + 1,
                        metadata_json={
                            **t.metadata_json,
                            "source_hash": source_hash(b.source_text),
                            "ai_repair_job_id": job.id,
                        },
                    )
                )
                saved = session.get(LayoutResult, identity)
                for key in ("bbox", "font", "font_size", "line_breaks", "fit_status"):
                    setattr(saved, key, getattr(entry_layout, key))
                session.add(
                    Issue(
                        document_id=document.id,
                        block_id=identity,
                        page_number=stored.page_number,
                        code="ai_repair_review_required",
                        severity="warning",
                        stage="checker",
                        message="AI repair passed local rendering checks. Review the updated wording and appearance.",
                    )
                )
            session.flush()
            interchange = []
            for b in session.scalars(
                select(Block).where(Block.document_id == document.id)
            ):
                t = latest(session, b.id, document.target_language, document.script)
                if rich(b) and t and not b.style.get("retired"):
                    interchange.append((b, t))
            if interchange:
                (stage / "translation.xlf").write_bytes(
                    export_xliff(interchange, document.target_language)
                )
                publications.append(
                    (stage / "translation.xlf", root / "translation.xlf")
                )
            backups = []
            try:
                for index, (candidate, destination) in enumerate(publications):
                    backup = stage / f"backup-{index}"
                    if destination.exists():
                        shutil.copy2(destination, backup)
                    backups.append((destination, backup))
                    cancel.check()
                    candidate.replace(destination)
                job.payload = {
                    **job.payload,
                    "repair_results": records,
                    "accepted": len(accepted),
                    "repair_published": True,
                }
                cancel.check()
                session.commit()
            except BaseException:
                session.rollback()
                for destination, backup in reversed(backups):
                    if backup.exists():
                        backup.replace(destination)
                    else:
                        destination.unlink(missing_ok=True)
                raise
        else:
            job.payload = {
                **job.payload,
                "repair_results": records,
                "accepted": 0,
                "repair_published": True,
            }
            session.commit()
    return "completed_with_issues"
