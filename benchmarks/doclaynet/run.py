"""Offline rendering suite: no translation, OCR, vision or other model calls."""

import argparse
import hashlib
import html
import json
import platform
import re
import shutil
import subprocess
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pymupdf as fitz
from sqlalchemy import select

from app.agents.inspector import InspectorAgent
from app.agents.text_reader import TextReaderAgent
from app.agents.translator import LITERALS, source_hash
from app.config import Settings
from app.models import Block, Document, Issue, LayoutResult, Page, Translation, database
from app.orchestrator import Orchestrator
from app.services.rich_text import format_hash, rich, source_runs
from app.services.storage import Storage
from app.services.text_fitter import FontRegistry

PROFILES = {
    "identity": "en",
    "expand150": "en",
    "hindi": "hi",
    "tamil": "ta",
    "urdu": "ur",
}
HARD_CODES = {
    "clipping",
    "missing_text",
    "collision",
    "graphics_changed",
    "page_dimensions_changed",
    "protected_value_changed",
}


class NoNetwork:
    async def translate(self, *args, **kwargs):
        raise AssertionError(
            "Translation calls are forbidden in this rendering benchmark"
        )

    async def extract(self, *args, **kwargs):
        raise AssertionError("OCR calls are forbidden in this rendering benchmark")


def replacement(text, profile, protected_ranges=()):
    """Synthetic strings only. Preserve literal values and all whitespace.

    Character-local expansion keeps replacement stable across paragraph/run
    boundaries. expand150 expands roughly half of Latin letters; actual growth
    is reported, rather than assuming every string grows exactly 50 percent.
    """
    if profile == "identity":
        return text
    protected = [(m.start(), m.end()) for m in LITERALS.finditer(text)] + list(
        protected_ranges
    )
    syllables = {"hindi": "क", "tamil": "க", "urdu": "ب"}
    output = []
    for index, char in enumerate(text):
        if not char.isalpha() or any(a <= index < b for a, b in protected):
            output.append(char)
        elif profile == "expand150":
            output.append(char + ("x" if ord(char) % 2 == 0 else ""))
        else:
            output.append(syllables[profile])
    return "".join(output)


def canonical_replacements(blocks, extracted, profile):
    """Protect literals crossing style spans before producing fixed fixtures."""
    lines = {}
    for pn, page in enumerate(extracted, 1):
        raw = [b for b in page["blocks"] if b["type"] == 0]
        for bi, block in enumerate(raw):
            for li, line in enumerate(block["lines"]):
                texts = [span["text"] for span in line["spans"]]
                starts, cursor = [], 0
                for text in texts:
                    starts.append(cursor)
                    cursor += len(text)
                matches = [
                    (m.start(), m.end()) for m in LITERALS.finditer("".join(texts))
                ]
                lines[(pn, bi, li)] = texts, starts, matches
    result = {}
    for block in blocks:
        match = re.search(r"-p(\d+)-b(\d+)-l(\d+)-s(\d+)$", block.id)
        pn, bi, li, si = map(int, match.groups())
        texts, starts, matches = lines[(pn, bi, li)]
        offset = starts[si] + texts[si].find(block.source_text)
        length = len(block.source_text)
        ranges = [
            (max(0, a - offset), min(length, b - offset))
            for a, b in matches
            if a < offset + length and b > offset
        ]
        result[block.id] = replacement(block.source_text, profile, ranges)
    return result


def translations_for(block, canonical, profile):
    ir = rich(block)
    if not ir:
        return canonical[block.id], {}
    runs = []
    # Recover the exact same per-source-span replacement in every layout mode.
    # Only inter-span whitespace is allowed to differ during paragraph reflow.
    source_text = {s["id"]: s["text"] for s in ir["source_spans"]}
    for run in source_runs(block):
        parts, cursor = [], 0
        for identity in run["source_ids"]:
            original = source_text[identity]
            start = run["text"].find(original, cursor)
            if start < 0:
                raise ValueError("Cannot align canonical replacement to source run")
            parts.extend([run["text"][cursor:start], canonical[identity]])
            cursor = start + len(original)
        parts.append(run["text"][cursor:])
        runs.append({**run, "text": "".join(parts)})
    return "".join(r["text"] for r in runs), {"rich_text": {"runs": runs}}


def render_case(source, case_dir, mode, profile, fonts_dir):
    case_dir.mkdir(parents=True, exist_ok=False)
    storage = Storage(case_dir / "state")
    engine, sessions = database(storage.root / "studio.sqlite3")
    settings = Settings(
        _env_file=None,
        studio_data_dir=storage.root,
        studio_font_dir=fonts_dir,
        studio_cache_enabled=False,
        studio_vision_review_enabled=False,
    )
    fonts = FontRegistry(fonts_dir)
    language = PROFILES[profile]
    if fonts.resolve(language, "native") == "missing":
        engine.dispose()
        raise ValueError(f"Missing font for {language}; provide --fonts")
    orchestrator = Orchestrator(
        sessions,
        storage,
        settings,
        NoNetwork(),
        fonts,
        None,
        NoNetwork(),
    )
    document_id = "page"
    shutil.copyfile(source, storage.path(document_id, "source.pdf"))
    started = time.perf_counter()
    try:
        metadata = InspectorAgent().run(source)
        if len(metadata) != 1 or metadata[0]["extraction_method"] != "digital":
            raise ValueError(
                "Expected a single selectable-text PDF page; OCR is disabled"
            )
        with sessions() as session:
            session.add(
                Document(
                    id=document_id,
                    filename=source.name,
                    sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    target_language=language,
                    script="native",
                    page_count=1,
                )
            )
            session.flush()
            item = metadata[0]
            session.add(
                Page(
                    id="page-p1",
                    document_id=document_id,
                    number=1,
                    width=item["width"],
                    height=item["height"],
                    metadata_json={**item, "layout_mode": "spans"},
                )
            )
            session.commit()
        orchestrator.refresh_geometry(document_id)
        with sessions() as session:
            originals = list(
                session.scalars(select(Block).where(Block.document_id == document_id))
            )
            canonical = canonical_replacements(
                originals, TextReaderAgent().run(source), profile
            )
            original_count = len(originals)
            page = session.get(Page, "page-p1")
            page.metadata_json = {**page.metadata_json, "layout_mode": mode}
            session.commit()
        orchestrator.refresh_geometry(document_id)
        with sessions() as session:
            blocks = [
                b for b in session.scalars(select(Block)) if not b.style.get("retired")
            ]
            if not blocks:
                raise ValueError(
                    "No extracted blocks; an empty page cannot count as a successful case"
                )
            for block in blocks:
                text, extra = translations_for(block, canonical, profile)
                session.add(
                    Translation(
                        block_id=block.id,
                        target_language=language,
                        script="native",
                        text=text,
                        revision=1,
                        metadata_json={
                            "source_hash": source_hash(block.source_text),
                            "format_hash": format_hash(block),
                            **extra,
                        },
                    )
                )
            session.commit()
        orchestrator.render(document_id)
        with sessions() as session:
            records = []
            for block in blocks:
                layout = session.get(LayoutResult, block.id)
                members = (
                    rich(block)["source_spans"] if rich(block) else [{"id": block.id}]
                )
                text = session.scalar(
                    select(Translation).where(Translation.block_id == block.id)
                ).text
                records.append(
                    {
                        "id": block.id,
                        "source_span_ids": [s["id"] for s in members],
                        "source": block.source_text,
                        "replacement": text,
                        "source_size": block.style["size"],
                        "font_size": layout.font_size if layout else None,
                        "shrink_ratio": layout.font_size / block.style["size"]
                        if layout
                        else None,
                        "fit_status": layout.fit_status if layout else "missing_layout",
                        "bbox": layout.bbox if layout else None,
                        "table_cell": block.style.get("table_cell_bbox"),
                        "style": block.style,
                        "line_breaks": layout.line_breaks if layout else [],
                    }
                )
            issues = [
                {
                    "code": i.code,
                    "severity": i.severity,
                    "block_id": i.block_id,
                    "message": i.message,
                }
                for i in session.scalars(
                    select(Issue).where(Issue.resolution != "resolved")
                )
            ]
        fitted = [r for r in records if r["fit_status"] == "fitted"]
        covered = [identity for r in records for identity in r["source_span_ids"]]
        if Counter(covered) != Counter(canonical.keys()):
            raise AssertionError("Grouping lost or duplicated source spans")
        source_folder = storage.root / document_id
        artifact = (
            "final.pdf" if (source_folder / "final.pdf").exists() else "draft.pdf"
        )
        raw_output_bytes = (source_folder / artifact).stat().st_size
        with fitz.open(source_folder / artifact) as raw_pdf:
            raw_pdf.save(case_dir / artifact, garbage=4, deflate=True)
        # Keep the database and source; discard only redundant generated copies.
        for generated in ("page-1.pdf", "page-1.png", artifact):
            (source_folder / generated).unlink(missing_ok=True)
        with fitz.open(case_dir / artifact) as pdf:
            pdf[0].get_pixmap(matrix=fitz.Matrix(0.8, 0.8), alpha=False).save(
                str(case_dir / "preview.jpg")
            )
        fitted_spans = sum(len(r["source_span_ids"]) for r in fitted)
        summary = {
            "status": "review" if issues or fitted_spans != original_count else "pass",
            "artifact": artifact,
            "raw_output_bytes": raw_output_bytes,
            "output_bytes": (case_dir / artifact).stat().st_size,
            "seconds": round(time.perf_counter() - started, 3),
            "source_spans": original_count,
            "blocks": len(records),
            "fitted_blocks": len(fitted),
            "rendered_source_spans": fitted_spans,
            "retained_source_spans": original_count - fitted_spans,
            "small_fitted_blocks": sum(r["font_size"] < 6 for r in fitted),
            "small_text_roles": dict(
                Counter(
                    "text"
                    if any(c.isalpha() for c in r["source"])
                    else "numeric"
                    if any(c.isdigit() for c in r["source"])
                    else "punctuation_or_symbol"
                    for r in fitted
                    if r["font_size"] < 6
                )
            ),
            "small_below_original_floor": sum(
                r["font_size"] < min(6, r["source_size"]) - 0.05 for r in fitted
            ),
            "min_font_size": min((r["font_size"] for r in fitted), default=None),
            "min_shrink_ratio": min((r["shrink_ratio"] for r in fitted), default=None),
            "character_growth": round(
                sum(len(t) for t in canonical.values())
                / max(1, sum(len(b.source_text) for b in originals)),
                3,
            ),
            "issue_counts": dict(Counter(i["code"] for i in issues)),
            "hard_issue_count": sum(i["code"] in HARD_CODES for i in issues),
            "issues": issues,
            "layouts": records,
        }
        (case_dir / "result.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        )
        return summary
    finally:
        engine.dispose()


def report(out, results, provenance):
    compact = [
        {k: v for k, v in r.items() if k not in {"issues", "layouts"}} for r in results
    ]
    aggregate = []
    for mode, profile in sorted({(r["mode"], r["profile"]) for r in results}):
        rows = [r for r in results if (r["mode"], r["profile"]) == (mode, profile)]
        counts = Counter()
        for row in rows:
            counts.update(row.get("issue_counts", {}))
        aggregate.append(
            {
                "mode": mode,
                "profile": profile,
                "cases": len(rows),
                "errors": sum(r["status"] == "error" for r in rows),
                "retained_source_spans": sum(
                    r.get("retained_source_spans", 0) for r in rows
                ),
                "small_fitted_blocks": sum(
                    r.get("small_fitted_blocks", 0) for r in rows
                ),
                "issue_counts": dict(counts),
            }
        )
    (out / "summary.json").write_text(
        json.dumps(
            {"provenance": provenance, "aggregate": aggregate, "cases": compact},
            indent=2,
        )
        + "\n"
    )
    rows_html = []
    for r in compact:
        folder = r["folder"]
        issues = html.escape(json.dumps(r.get("issue_counts", {})))
        rows_html.append(
            f"<tr><td>{r['page']}<br>{html.escape(r['category'])}<br>{r['mode']} / {r['profile']}</td>"
            f"<td>{r['status']}<br>retained spans: {r.get('retained_source_spans', '—')}<br>"
            f"minimum font: {r.get('min_font_size', '—')}<br>{issues}"
            f'<br>{html.escape(r.get("error", ""))}<br><a href="{folder}/result.json">Details</a></td>'
            f'<td><a href="sources/{r["page"]}.pdf"><img loading="lazy" src="sources/{r["page"]}.jpg"></a></td>'
            f'<td><a href="{folder}/{r.get("artifact", "final.pdf")}"><img loading="lazy" src="{folder}/preview.jpg"></a></td></tr>'
        )
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>DocLayNet rendering suite</title>'
        "<style>body{font:15px system-ui;margin:24px;background:#f5f6fa}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ccd;padding:10px;vertical-align:top}img{max-width:100%;width:420px}td:first-child{width:12%}"
        "td:nth-child(2){width:20%}</style><h1>DocLayNet rendering suite</h1>"
        "<p>Synthetic replacements; translation quality is not evaluated. Click either preview to open its PDF. "
        "Automated checks are diagnostics, not proof of visual fidelity. Review styles, table borders and glyph shaping manually.</p>"
        "<pre>" + html.escape(json.dumps(aggregate, indent=2)) + "</pre>"
        "<table><tr><th>Case</th><th>Diagnostics</th><th>Source</th><th>Rendered</th></tr>"
        + "".join(rows_html)
        + "</table>"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("data/benchmarks/doclaynet40")
    )
    parser.add_argument(
        "--out", type=Path, default=Path("output/benchmarks/doclaynet40")
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["spans", "paragraphs", "auto"],
        default=["spans", "paragraphs", "auto"],
    )
    parser.add_argument(
        "--profiles", nargs="+", choices=PROFILES, default=["identity", "expand150"]
    )
    parser.add_argument(
        "--fonts",
        type=Path,
        help="Optional Noto font directory; required for native script profiles",
    )
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Separate rendering processes (PyMuPDF is not shared between threads)",
    )
    args = parser.parse_args()
    if args.limit < 1 or args.workers < 1:
        parser.error("--limit and --workers must be positive")
    if args.out.exists():
        parser.error(
            "Output directory already exists; choose a new --out to preserve previous results"
        )
    manifest_path = args.root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    pages = manifest["pages"][: args.limit]
    if len(pages) != args.limit:
        parser.error("Manifest does not contain the requested page count")
    args.out.mkdir(parents=True)
    (args.out / "sources").mkdir()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], text=True, capture_output=True
    ).stdout.strip()
    provenance = {
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "git_revision": revision,
        "tracked_diff_sha256": hashlib.sha256(
            subprocess.run(["git", "diff", "HEAD"], capture_output=True).stdout
        ).hexdigest(),
        "fixture_version": 2,
        "source_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path("app").rglob("*.py"))
        },
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "font_directory": str(args.fonts.resolve()) if args.fonts else None,
        "font_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(args.fonts.rglob("*"))
            if p.suffix.lower() in {".ttf", ".otf"}
        }
        if args.fonts
        else {},
        "python": platform.python_version(),
        "pymupdf": fitz.VersionBind,
        "profiles": args.profiles,
        "modes": args.modes,
        "network_model_calls": 0,
        "page_count": len(pages),
        "settings": "Typography enabled (default); vision and OCR disabled; isolated SQLite per case",
    }
    shutil.copyfile(manifest_path, args.out / "manifest.json")
    results, jobs = [], []
    for index, page in enumerate(pages, 1):
        key = f"{index:02d}"
        source = args.root / page["pdf"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != page["sha256"]:
            raise ValueError(f"Checksum mismatch: {source}")
        shutil.copyfile(source, args.out / "sources" / f"{key}.pdf")
        with fitz.open(source) as pdf:
            pdf[0].get_pixmap(matrix=fitz.Matrix(0.8, 0.8), alpha=False).save(
                str(args.out / "sources" / f"{key}.jpg")
            )
        for profile in args.profiles:
            for mode in args.modes:
                folder = f"{key}-{profile}-{mode}"
                jobs.append((source, key, page, mode, profile, folder))
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending = {
            pool.submit(
                render_case, source, args.out / folder, mode, profile, args.fonts
            ): (key, page, mode, profile, folder)
            for source, key, page, mode, profile, folder in jobs
        }
        for future in as_completed(pending):
            key, page, mode, profile, folder = pending[future]
            try:
                summary = future.result()
            except Exception as exc:
                summary = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                (args.out / folder).mkdir(exist_ok=True)
                (args.out / folder / "result.json").write_text(
                    json.dumps(summary, indent=2)
                )
            results.append(
                {
                    "page": key,
                    "category": page.get("doc_category", "unknown"),
                    "mode": mode,
                    "profile": profile,
                    "folder": folder,
                    **summary,
                }
            )
            results.sort(key=lambda r: (r["page"], r["profile"], r["mode"]))
            report(args.out, results, provenance)
            print(
                f"{len(results)}/{len(jobs)} {folder}: {summary['status']} "
                f"{summary.get('error', summary.get('issue_counts'))}",
                flush=True,
            )
    return 1 if any(r["status"] == "error" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
