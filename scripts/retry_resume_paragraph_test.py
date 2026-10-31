"""Retry only failed/fallback groups in the explicitly authorized resume test.

Uses the isolated test database, not production data. Sends the same approved
resume text to the same Bodhan service; does not enable cross-request caching.
"""

import asyncio
import json
import shutil
import time
from collections import Counter
from pathlib import Path
from statistics import median

from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import Block, Document, Issue, Job, LayoutResult, Translation
from app.orchestrator import latest
from app.services.rich_text import format_hash
from scripts.test_resume_paragraph_live import MeasuredProvider

OUT = Path("output/pdf/resume-live-paragraph-test")


async def main():
    prior = json.loads((OUT / "results.json").read_text())
    initial = OUT / "initial-results.json"
    if not initial.exists():
        initial.write_text(json.dumps(prior, ensure_ascii=False, indent=2))
    settings = Settings()
    calls = []
    provider = MeasuredProvider(settings.bodhan_api_key, calls)
    app = create_app(
        Settings(
            studio_data_dir=OUT / "run-data",
            studio_font_dir=settings.studio_font_dir,
            studio_cache_enabled=False,
        ),
        provider,
    )
    orch = app.state.orchestrator
    start = time.perf_counter()
    stats = {}
    attempts = []
    with app.state.sessions() as session:
        doc = session.get(Document, prior["document_id"])
        job = session.get(Job, prior["job"]["id"])
        blocks = [
            b
            for b in session.scalars(
                select(Block)
                .where(Block.document_id == doc.id)
                .order_by(Block.reading_order)
            )
            if not b.style.get("retired")
        ]
        retry = []
        for block in blocks:
            t = latest(session, block.id, doc.target_language, doc.script)
            if not t or t.metadata_json.get("rich_text", {}).get("fallback"):
                retry.append(block)
        print(json.dumps({"retry_groups": len(retry)}), flush=True)
        for batch in orch.translator.batches(retry):
            async for block, text, metadata, error in orch.translator.translate_batch(
                batch, doc.target_language, doc.script, stats
            ):
                attempts.append(
                    {
                        "block_id": block.id,
                        "error": error.code if error else None,
                        "fallback": metadata.get("rich_text", {}).get("fallback")
                        if metadata
                        else None,
                    }
                )
                if error:
                    print(json.dumps(attempts[-1]), flush=True)
                    continue
                history = latest(session, block.id)
                session.add(
                    Translation(
                        block_id=block.id,
                        target_language=doc.target_language,
                        script=doc.script,
                        text=text,
                        revision=history.revision + 1 if history else 1,
                        metadata_json={
                            **metadata,
                            "job_id": job.id,
                            "format_hash": format_hash(block),
                        },
                    )
                )
                for issue in session.scalars(
                    select(Issue).where(
                        Issue.block_id == block.id, Issue.stage == "translator"
                    )
                ):
                    issue.resolution = "resolved"
                if metadata.get("rich_text", {}).get("fallback"):
                    session.add(
                        Issue(
                            document_id=doc.id,
                            block_id=block.id,
                            page_number=block.page_number,
                            code="inline_formatting_fallback",
                            stage="translator",
                            severity="warning",
                            message="Formatted runs translated separately; review sentence flow.",
                        )
                    )
                session.commit()
        did = doc.id
        jid = job.id
    await asyncio.to_thread(orch.render, did, jid)
    with app.state.sessions() as session:
        doc = session.get(Document, did)
        job = session.get(Job, jid)
        blocks = [
            b
            for b in session.scalars(select(Block).where(Block.document_id == did))
            if not b.style.get("retired")
        ]
        current = []
        sizes = []
        effective = []
        for b in blocks:
            t = latest(session, b.id, doc.target_language, doc.script)
            layout = session.get(LayoutResult, b.id)
            if t:
                current.append((b, t))
            if t and layout and layout.fit_status == "fitted":
                sizes.append(layout.font_size)
                effective.extend(
                    layout.font_size * sp["style"]["size"] / b.style["size"]
                    for sp in b.style.get("rich_text", {}).get("source_spans", [])
                )
        issues = list(
            session.scalars(
                select(Issue).where(
                    Issue.document_id == did, Issue.resolution != "resolved"
                )
            )
        )
        status = "completed_with_issues" if issues else "completed"
        job.status = doc.status = status
        job.progress = 1
        summary = {
            "total_blocks": len(blocks),
            "rendered_blocks": len(sizes),
            "retained_source_blocks": len(blocks) - len(sizes),
        }
        job.payload = {**job.payload, "result": summary, "repair_stats": stats}
        session.commit()

        def record(row):
            return {
                col.name: (
                    getattr(row, col.name).isoformat()
                    if hasattr(getattr(row, col.name), "isoformat")
                    else getattr(row, col.name)
                )
                for col in row.__table__.columns
            }

        issue_records = [record(i) for i in issues]
        newblocks = [
            {
                **record(b),
                "translation": record(
                    latest(session, b.id, doc.target_language, doc.script)
                )
                if latest(session, b.id, doc.target_language, doc.script)
                else None,
            }
            for b in blocks
        ]
        (OUT / "blocks.json").write_text(
            json.dumps(newblocks, ensure_ascii=False, indent=2)
        )
        result = {
            **prior,
            "status": status,
            "seconds": round(prior["seconds"] + time.perf_counter() - start, 3),
            "repair_seconds": round(time.perf_counter() - start, 3),
            "translated_paragraphs": len(current),
            "rendered_paragraphs": len(sizes),
            "formatting_fallback_paragraphs": sum(
                bool(t.metadata_json.get("rich_text", {}).get("fallback"))
                for _, t in current
            ),
            "logical_provider_calls": prior["logical_provider_calls"] + len(calls),
            "repair_provider_calls": calls,
            "repair_attempts": attempts,
            "repair_stats": stats,
            "issue_counts": dict(Counter(i.code for i in issues)),
            "issues": issue_records,
            "font_sizes": sizes,
            "median_paragraph_pt": median(sizes),
            "effective_span_median_pt": median(effective),
            "effective_spans_below_6pt": sum(x < 6 for x in effective),
            "job": record(job),
        }
        for name, dest in [
            ("final.pdf", "Resume - Tamil - paragraphs.pdf"),
            ("translation.xlf", "translation.xlf"),
            ("structure.json", "structure.json"),
        ]:
            shutil.copyfile(OUT / "run-data" / did / name, OUT / dest)
        (OUT / "results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(
            json.dumps(
                {
                    k: v
                    for k, v in result.items()
                    if k
                    not in {
                        "provider_calls",
                        "repair_provider_calls",
                        "font_sizes",
                        "job",
                        "issues",
                    }
                },
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for paid provider calls")
    asyncio.run(main())
