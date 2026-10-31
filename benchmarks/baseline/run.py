"""Save and run 100 digital pages through auto layout with fixed expansion.

No provider calls, production database access, parameter sweeps or mode matrix.
"""

import argparse
import hashlib
import html
import json
import math
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz

from app.services.typography import TypographyPass, neighbour_edges, penalty
from benchmarks.doclaynet.run import render_case, report


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def features(page):
    blocks = [b for b in page.get_text("dict")["blocks"] if b["type"] == 0]
    spans = [s for b in blocks for line in b["lines"] for s in line["spans"]]
    text = "".join(s["text"] for s in spans)
    letters = [c for c in text if c.isalpha()]
    return {
        "characters": len(text),
        "spans": len(spans),
        "text_blocks": len(blocks),
        "images": len(page.get_image_info()),
        "drawings": len(page.get_drawings()),
        "ascii_letter_fraction": sum(c.isascii() for c in letters)
        / max(1, len(letters)),
        "width": page.rect.width,
        "height": page.rect.height,
    }


def prepare(out):
    """40 pinned DocLayNet + 60 distinct ForMaT documents, no result-based selection."""
    sources = out / "sources"
    sources.mkdir()
    rows = []
    docroot = Path("data/benchmarks/doclaynet40")
    docmanifest = docroot / "manifest.json"
    for item in json.loads(docmanifest.read_text())["pages"]:
        original = docroot / item["pdf"]
        if digest(original) != item["sha256"]:
            raise ValueError(f"Checksum mismatch: {original}")
        with fitz.open(original) as pdf:
            info = features(pdf[0])
        rows.append(
            {
                "dataset": "DocLayNet",
                "document": item["doc_name"],
                "source_page_index": item["page_no"],
                "page_index_convention": "original dataset page_no, unchanged",
                "doc_category": item["doc_category"],
                "layout_counts": item["layout_counts"],
                "original_file": str(original.resolve()),
                "original_sha256": digest(original),
                "features": info,
                "copy": True,
            }
        )
    root = Path("data/benchmarks/format")
    manifest_path = root / "balanced-manifest.json"
    groups = defaultdict(list)
    for item in json.loads(manifest_path.read_text())["documents"]:
        groups[item["source"]].append(item)
    for values in groups.values():
        values.sort(key=lambda x: x["id"])
    selected = []
    # Round-robin publishers; within each document choose the densest of seven
    # evenly spaced pages (by span count, then characters). No renderer feedback.
    while len(selected) < 60:
        progressed = False
        for publisher in sorted(groups):
            while groups[publisher]:
                item = groups[publisher].pop(0)
                path = root / item["file"]
                if digest(path) != item["sha256"]:
                    raise ValueError(f"Checksum mismatch: {path}")
                with fitz.open(path) as pdf:
                    candidates = sorted(
                        {round(i * (len(pdf) - 1) / 6) for i in range(7)}
                    )
                    choices = [(index, features(pdf[index])) for index in candidates]
                choices = [
                    (index, f)
                    for index, f in choices
                    if f["characters"] >= 500 and f["ascii_letter_fraction"] >= 0.85
                ]
                if not choices:
                    continue
                index, info = max(
                    choices,
                    key=lambda pair: (
                        pair[1]["spans"],
                        pair[1]["characters"],
                        -pair[0],
                    ),
                )
                selected.append(
                    {
                        "dataset": "ForMaT",
                        "document": item["title"],
                        "document_id": item["id"],
                        "publisher": publisher,
                        "source_url": item["url"],
                        "source_page_index": index,
                        "page_index_convention": "zero-based",
                        "doc_category": publisher,
                        "features": info,
                        "sampled_page_indices": candidates,
                        "original_file": str(path.resolve()),
                        "original_sha256": digest(path),
                        "copy": False,
                    }
                )
                progressed = True
                break
            if len(selected) == 60:
                break
        if not progressed:
            raise ValueError(f"Only {len(selected)} eligible ForMaT documents")
    rows += selected
    for number, row in enumerate(rows, 1):
        key = f"{number:03d}"
        destination = sources / f"{key}.pdf"
        if row.pop("copy"):
            shutil.copyfile(row["original_file"], destination)
        else:
            with fitz.open(row["original_file"]) as original, fitz.open() as page_pdf:
                index = row["source_page_index"]
                page_pdf.insert_pdf(original, from_page=index, to_page=index)
                page_pdf.save(destination, garbage=4, deflate=True)
        with fitz.open(destination) as pdf:
            pdf[0].get_pixmap(matrix=fitz.Matrix(0.8, 0.8), alpha=False).save(
                str(sources / f"{key}.jpg")
            )
        row.update(id=key, pdf=f"sources/{key}.pdf", sha256=digest(destination))
        print(f"Prepared {key}/100 {row['dataset']} {row['doc_category']}", flush=True)
    manifest = {
        "selection": "40 pinned DocLayNet pages plus 60 distinct ForMaT documents; publisher round-robin; densest of seven evenly spaced eligible digital pages per document",
        "selection_limitations": "Convenience stress sample, not production-representative; no scans; >=500 characters and >=85% ASCII letters for ForMaT; extracted single pages do not test multipage behavior.",
        "input_manifests": {str(p): digest(p) for p in (docmanifest, manifest_path)},
        "pages": rows,
    }
    write_json(out / "manifest.json", manifest)
    return manifest


def run_case(source, directory, fonts):
    observations = []
    original = TypographyPass.apply

    def observed(instance, entries, page):
        edges = neighbour_edges(entries)
        before = penalty(entries, edges)
        result = original(instance, entries, page)
        observations.append(
            {
                "page": page.number,
                "ids": [b.id for b, _, _ in entries],
                "edges": edges,
                "before": before,
                "proposed": penalty(entries, edges),
                "refits": result["refits"],
                "changed_blocks": result["changed_blocks"],
            }
        )
        return result

    # This patch only observes the existing pass inside an isolated worker.
    with patch.object(TypographyPass, "apply", observed):
        result = render_case(source, directory, "auto", "expand150", fonts)
    layout = json.loads((directory / "state/page/layout-report.json").read_text())
    telemetry = []
    final_rows = {r["id"]: r for r in result["layouts"]}
    for item in observations:
        edges = item["edges"]
        ratios = [
            final_rows[key]["font_size"] / final_rows[key]["source_size"]
            for key in item["ids"]
        ]
        final = sum(
            w * math.log(max(ratios[i], 1e-6) / max(ratios[j], 1e-6)) ** 2
            for i, j, w in edges
        )
        telemetry.append(
            {
                "page": item["page"],
                "edge_count": len(edges),
                "weight_sum": sum(w for _, _, w in edges),
                "before_typography": item["before"],
                "proposed_after_typography": item["proposed"],
                "final_on_same_edges": final,
                "final_mean_weighted_penalty": final / sum(w for _, _, w in edges)
                if edges
                else None,
                "refits": item["refits"],
                "changed_blocks": item["changed_blocks"],
                "fixed_edges": [
                    {"a": item["ids"][i], "b": item["ids"][j], "weight": w}
                    for i, j, w in edges
                ],
            }
        )
    write_json(
        directory / "consistency.json",
        {
            "formula": "sum(w_ij * log((output_i/source_i)/(output_j/source_j))**2)",
            "scope": "Before/after typography only, after cell and paragraph flow; final uses the same edges and reflects page rollback.",
            "pages": telemetry,
        },
    )
    write_json(directory / "layout-report.json", layout)
    result["consistency"] = [
        {k: v for k, v in t.items() if k != "fixed_edges"} for t in telemetry
    ]
    result["cell_changed_blocks"] = sum(len(p["cell_changes"]) for p in layout["pages"])
    result["paragraph_changed_blocks"] = sum(
        len(p["paragraph_changes"]) for p in layout["pages"]
    )
    result["page_changes_accepted"] = sum(p["change_accepted"] for p in layout["pages"])
    result["translation_retained_blocks"] = sum(
        b["translation_retained"] for p in layout["pages"] for b in p["blocks"]
    )
    # Independently reopen the delivered artifact; diagnostics remain hypotheses.
    with fitz.open(source) as src, fitz.open(directory / result["artifact"]) as pdf:
        result["artifact_validation"] = {
            "opens": True,
            "page_count": len(pdf),
            "page_dimensions_preserved": len(pdf) == 1 and pdf[0].rect == src[0].rect,
            "searchable_characters": sum(len(p.get_text()) for p in pdf),
            "sha256": digest(directory / result["artifact"]),
        }
    write_json(directory / "result.json", result)
    return result


def save_report(out, results, provenance, pages):
    report(out, results, provenance)
    counts = Counter(r["status"] for r in results)
    issues = Counter()
    for row in results:
        issues.update(row.get("issue_counts", {}))
    summary = {
        "completed": len(results),
        "planned": len(pages),
        "status_counts": dict(counts),
        "stop_reason": provenance.get("stop_reason", "running"),
        "deadline_utc": provenance.get("deadline_utc"),
        "interrupted_cases": provenance.get("interrupted_cases", []),
        "issue_counts": dict(issues),
        "retained_source_spans": sum(
            r.get("retained_source_spans", 0) for r in results
        ),
        "rendered_source_spans": sum(
            r.get("rendered_source_spans", 0) for r in results
        ),
        "cell_changed_blocks": sum(r.get("cell_changed_blocks", 0) for r in results),
        "paragraph_changed_blocks": sum(
            r.get("paragraph_changed_blocks", 0) for r in results
        ),
        "consistency": {
            "before": sum(
                t["before_typography"]
                for r in results
                for t in r.get("consistency", [])
            ),
            "proposed": sum(
                t["proposed_after_typography"]
                for r in results
                for t in r.get("consistency", [])
            ),
            "final_same_edges": sum(
                t["final_on_same_edges"]
                for r in results
                for t in r.get("consistency", [])
            ),
        },
        "not_run": sorted(set(p["id"] for p in pages) - {r["page"] for r in results}),
        "limitations": [
            "Synthetic Latin expansion, not translation accuracy or native script validation",
            "Single digital pages, no OCR or multipage job tests",
            "Checker findings are not independently confirmed defects",
            "No feature ablations, model calls, or comprehensive human review",
        ],
    }
    write_json(out / "baseline-summary.json", summary)
    # Separate, filterable issue inventory with links to each case's evidence.
    rows = []
    for r in results:
        rows.append(
            '<tr data-status="'
            + r["status"]
            + '"><td>'
            + r["page"]
            + "</td><td>"
            + html.escape(r["category"])
            + "</td><td>"
            + r["status"]
            + "</td><td>"
            + str(r.get("retained_source_spans", ""))
            + "</td><td>"
            + html.escape(json.dumps(r.get("issue_counts", {})))
            + '</td><td><a href="sources/'
            + r["page"]
            + '.pdf">Source</a> | <a href="'
            + r["folder"]
            + "/"
            + r.get("artifact", "final.pdf")
            + '">Output</a> | <a href="'
            + r["folder"]
            + '/result.json">Details</a> | <a href="'
            + r["folder"]
            + '/layout-report.json">Passes</a> | <a href="'
            + r["folder"]
            + '/consistency.json">Formula</a></td></tr>'
        )
    (out / "review.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>100-page auto baseline</title><style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;text-align:left}pre{white-space:pre-wrap}</style><h1>100-page auto baseline</h1><p>Fixed synthetic expansion. Review means diagnostic findings, not a confirmed defect. <a href="index.html">Side-by-side page previews</a></p><pre>'
        + html.escape(json.dumps(summary, indent=2))
        + "</pre><label>Filter <select onchange=\"document.querySelectorAll('tr[data-status]').forEach(r=>r.hidden=this.value!=='all'&&r.dataset.status!==this.value)\"><option>all</option><option>review</option><option>error</option><option>pass</option></select></label><table><tr><th>Page</th><th>Category</th><th>Status</th><th>Retained spans</th><th>Findings</th><th>Evidence</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fonts", type=Path, default=Path("fonts"))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument(
        "--deadline-utc", help="Absolute UTC deadline; overrides --minutes"
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--case", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.fonts = args.fonts.resolve()
    if args.case:
        directory = args.out / f"{args.case}-expand150-auto"
        try:
            run_case(args.out / "sources" / f"{args.case}.pdf", directory, args.fonts)
        except Exception as exc:
            directory.mkdir(exist_ok=True)
            (directory / "error.log").write_text(traceback.format_exc())
            write_json(
                directory / "result.json",
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
            )
            return 1
        return 0
    if args.workers < 1 or args.minutes <= 0:
        parser.error("Workers and minutes must be positive")
    started = time.monotonic()
    deadline = (
        datetime.fromisoformat(args.deadline_utc).timestamp()
        if args.deadline_utc
        else time.time() + args.minutes * 60
    )
    if args.resume:
        manifest = json.loads((args.out / "manifest.json").read_text())
    else:
        args.out.mkdir(parents=True, exist_ok=False)
        manifest = prepare(args.out)
    pages = manifest["pages"]
    provenance = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "git_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip(),
        "manifest_sha256": digest(args.out / "manifest.json"),
        "page_count": 100,
        "source_sha256": {str(p): digest(p) for p in sorted(Path("app").rglob("*.py"))},
        "harness_sha256": {
            str(p): digest(p)
            for p in [Path(__file__), Path("benchmarks/doclaynet/run.py")]
        },
        "font_sha256": {
            str(p): digest(p)
            for p in sorted(args.fonts.rglob("*"))
            if p.suffix.lower() in {".ttf", ".otf"}
        },
        "python": platform.python_version(),
        "pymupdf": fitz.VersionBind,
        "profiles": ["expand150"],
        "modes": ["auto"],
        "network_model_calls": 0,
        "fixture_version": 2,
        "workers": args.workers,
        "settings": {
            "typography_enabled": True,
            "minimum_target_pt": 6,
            "max_refits": 24,
            "cache": False,
            "mode": "auto",
            "profile": "expand150",
        },
    }
    if args.resume:
        previous = json.loads((args.out / "provenance.json").read_text())
        if previous["source_sha256"] != provenance["source_sha256"]:
            raise ValueError("Application changed: cannot resume the same baseline")
        provenance["previous_segments"] = previous.get("previous_segments", []) + [
            {k: v for k, v in previous.items() if k != "previous_segments"}
        ]
    provenance["deadline_utc"] = datetime.fromtimestamp(
        deadline, timezone.utc
    ).isoformat()
    # Workers import a frozen code snapshot so workspace edits cannot mix versions.
    snapshot = args.out / "code-snapshot"
    if not snapshot.exists():
        snapshot.mkdir()
        for name in ("app", "benchmarks"):
            shutil.copytree(
                name,
                snapshot / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
    for name, expected in provenance["source_sha256"].items():
        if digest(snapshot / name) != expected:
            raise ValueError(f"Frozen source differs: {name}")
    write_json(args.out / "provenance.json", provenance)
    results = []
    pending = {}
    remaining = []
    for page in pages:
        folder = f"{page['id']}-expand150-auto"
        directory = args.out / folder
        result_path = directory / "result.json"
        if result_path.exists() and (directory / "consistency.json").exists():
            result = json.loads(result_path.read_text())
            results.append(
                {
                    "page": page["id"],
                    "category": page["doc_category"],
                    "mode": "auto",
                    "profile": "expand150",
                    "folder": folder,
                    **result,
                }
            )
        else:
            # Preserve the previous interrupted attempt, never overwrite it.
            if directory.exists():
                directory.rename(
                    directory.with_name(folder + "-interrupted-" + str(time.time_ns()))
                )
            remaining.append(page)
    queue = list(remaining)
    stop_requested = []

    def request_stop(signum, frame):
        stop_requested.append(f"signal_{signum}")

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(snapshot)
    # Avoid inheriting settings that would change the declared default baseline.
    for key in list(env):
        if key.startswith("STUDIO_"):
            env.pop(key)

    def stop_reason():
        if (args.out / "STOP").exists():
            return "stop_file"
        if stop_requested:
            return stop_requested[0]
        return "deadline" if time.time() >= deadline else None

    save_report(args.out, results, provenance, pages)
    last_heartbeat = 0
    reason = None
    try:
        while True:
            reason = stop_reason()
            if reason:
                break
            while len(pending) < args.workers and queue:
                page = queue.pop(0)
                directory = args.out / f"{page['id']}-expand150-auto"
                log = (args.out / f"worker-{page['id']}.log").open("a")
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        "-m",
                        "benchmarks.baseline.run",
                        "--out",
                        str(args.out),
                        "--fonts",
                        str(args.fonts),
                        "--case",
                        page["id"],
                    ],
                    cwd=snapshot,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                pending[process.pid] = (process, page, log)
            for pid, (process, page, log) in list(pending.items()):
                if process.poll() is None:
                    continue
                log.close()
                pending.pop(pid)
                folder = f"{page['id']}-expand150-auto"
                result_path = args.out / folder / "result.json"
                result = (
                    json.loads(result_path.read_text())
                    if result_path.exists()
                    else {
                        "status": "error",
                        "error": f"Worker exited {process.returncode} without a result; see worker log",
                    }
                )
                results.append(
                    {
                        "page": page["id"],
                        "category": page["doc_category"],
                        "mode": "auto",
                        "profile": "expand150",
                        "folder": folder,
                        **result,
                    }
                )
                results.sort(key=lambda r: r["page"])
                save_report(args.out, results, provenance, pages)
                print(
                    f"{len(results)}/100 {folder}: {result['status']} {result.get('error', result.get('issue_counts'))}",
                    flush=True,
                )
            if not pending and not queue:
                break
            if time.monotonic() - last_heartbeat >= 30:
                print(
                    f"Heartbeat: {len(results)}/100 complete, {len(pending)} active; deadline {provenance['deadline_utc']}",
                    flush=True,
                )
                last_heartbeat = time.monotonic()
            time.sleep(0.5)
    finally:
        interrupted = []
        for process, page, log in pending.values():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                interrupted.append(page["id"])
        for process, page, log in pending.values():
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            log.close()
        provenance["stop_reason"] = reason or "completed"
        provenance["interrupted_cases"] = interrupted
    provenance["elapsed_seconds"] = round(time.monotonic() - started, 3)
    provenance["finished_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(args.out / "provenance.json", provenance)
    save_report(args.out, results, provenance, pages)
    return int(len(results) != 100 or any(r["status"] == "error" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
