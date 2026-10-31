"""Actual provider translation baseline; preserves PDFs, jobs and partial runs."""

import argparse
import html
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pymupdf as fitz
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.bodhan import LANGUAGES
from benchmarks.baseline.run import digest, write_json


def translate_one(root, case):
    directory = root / case["id"]
    directory.mkdir(exist_ok=False)
    started = time.monotonic()
    result = {
        "case": case,
        "status": "running",
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(directory / "result.json", result)
    settings = Settings().model_copy(
        update={
            "studio_data_dir": directory / "studio",
            "studio_workers": 1,
            "studio_cache_enabled": False,
        }
    )
    try:
        with TestClient(create_app(settings)) as client:

            def save_response(name, response):
                response.raise_for_status()
                value = response.json()
                write_json(directory / name, value)
                return value

            def wait_job(response, stage):
                job_id = save_response(stage + "-submission.json", response)["job_id"]
                while True:
                    job = save_response(
                        stage + "-job.json", client.get("/v1/jobs/" + job_id)
                    )
                    if job["status"] in {
                        "completed",
                        "completed_with_issues",
                        "failed",
                        "cancelled",
                    }:
                        return job
                    time.sleep(0.5)

            source = root / "sources" / f"{case['page']}.pdf"
            if digest(source) != case["source_sha256"]:
                raise ValueError("Source checksum mismatch")
            submission = client.post(
                "/v1/documents",
                files={"file": ("source.pdf", source.read_bytes(), "application/pdf")},
            )
            inspection = wait_job(submission, "inspection")
            document = submission.json()["document_id"]
            if inspection["status"] == "failed":
                raise RuntimeError(
                    "Inspection failed: " + str(inspection.get("error_code"))
                )
            job = wait_job(
                client.post(
                    f"/v1/documents/{document}/translations",
                    json={
                        "target_language": case["language"],
                        "script": "native",
                        "layout_mode": "auto",
                        "transliterate": False,
                    },
                ),
                "translation",
            )
            for suffix, name in [
                ("", "document.json"),
                ("/issues", "issues.json"),
                ("/blocks", "blocks.json"),
            ]:
                save_response(name, client.get(f"/v1/documents/{document}" + suffix))
            result.update(
                status=job["status"],
                document_id=document,
                translation_result=job.get("payload", {}),
                error_code=job.get("error_code"),
            )
            for name in (
                "final.pdf",
                "draft.pdf",
                "layout-report.json",
                "structure.json",
                "translation.xlf",
            ):
                response = client.get(f"/v1/documents/{document}/artifacts/{name}")
                if response.status_code == 200:
                    (directory / name).write_bytes(response.content)
            artifact = next(
                (
                    name
                    for name in ("final.pdf", "draft.pdf")
                    if (directory / name).exists()
                ),
                None,
            )
            result["artifact"] = artifact
            if artifact:
                with fitz.open(directory / artifact) as pdf:
                    result.update(
                        output_pages=len(pdf),
                        output_sha256=digest(directory / artifact),
                    )
                    pdf[0].get_pixmap(matrix=fitz.Matrix(0.8, 0.8), alpha=False).save(
                        str(directory / "preview.jpg")
                    )
            result["issue_counts"] = dict(
                Counter(
                    i["code"]
                    for i in json.loads((directory / "issues.json").read_text())
                )
            )
    except Exception as exc:
        result.update(status="error", error=f"{type(exc).__name__}: {exc}")
        (directory / "error.log").write_text(traceback.format_exc())
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_json(directory / "result.json", result)


def report(root, plan, reason="running"):
    rows = []
    for case in plan["cases"]:
        p = root / case["id"] / "result.json"
        r = (
            json.loads(p.read_text())
            if p.exists()
            else {"case": case, "status": "not_started"}
        )
        rows.append(r)
    write_json(
        root / "summary.json",
        {
            "stop_reason": reason,
            "deadline_utc": plan["deadline_utc"],
            "planned": len(rows),
            "status_counts": dict(Counter(r["status"] for r in rows)),
            "cases": rows,
        },
    )
    body = []
    for r in rows:
        case = r["case"]
        key = case["id"]
        links = f'<a href="{key}/result.json">Result</a>'
        if r.get("artifact"):
            links += f' | <a href="{key}/{r["artifact"]}">PDF</a> | <a href="{key}/layout-report.json">Layout</a> | <a href="{key}/translation-job.json">Job</a>'
        body.append(
            f"<tr><td>{key}</td><td>{html.escape(r['status'])}</td><td>{html.escape(json.dumps(r.get('issue_counts', {})))}</td><td>{links}</td></tr>"
        )
    (root / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Actual translation baseline</title><style>body{font:15px system-ui;margin:24px}td,th{padding:10px;border:1px solid #ccc}table{border-collapse:collapse}</style><h1>Actual translation baseline</h1><p>Actual Bodhan translations through auto layout. Automated findings require visual review.</p><p>'
        + html.escape(reason)
        + "</p><table><tr><th>Case</th><th>Status</th><th>Findings</th><th>Evidence</th></tr>"
        + "".join(body)
        + "</table>"
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--source-suite", type=Path)
    p.add_argument("--languages", nargs="+", choices=sorted(LANGUAGES))
    p.add_argument("--deadline-utc")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--case", help=argparse.SUPPRESS)
    p.add_argument("--live", action="store_true")
    p.add_argument(
        "--balanced",
        action="store_true",
        help="Assign one target per document, equal counts",
    )
    p.add_argument("--seed", type=int, default=20261029)
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()
    root = args.out.resolve()
    if args.case:
        plan = json.loads((root / "plan.json").read_text())
        translate_one(root, next(c for c in plan["cases"] if c["id"] == args.case))
        return
    if (
        not args.live
        or not args.languages
        or not args.source_suite
        or not args.deadline_utc
    ):
        p.error("--live, --languages, --source-suite and --deadline-utc are required")
    if args.workers < 1:
        p.error("--workers must be positive")
    if not Settings().bodhan_api_key:
        p.error("Translation key is not configured")
    root.mkdir(parents=True, exist_ok=args.resume)
    source = args.source_suite.resolve()
    manifest = json.loads((source / "manifest.json").read_text())
    if not args.resume:
        shutil.copytree(source / "sources", root / "sources")
    write_json(root / "manifest.json", manifest)
    deadline = datetime.fromisoformat(args.deadline_utc).timestamp()
    languages = list(dict.fromkeys(args.languages))
    if args.balanced:
        if len(manifest["pages"]) % len(languages):
            p.error("Page count must divide evenly across target languages")
        rng = random.Random(args.seed)
        assignments = []
        for _ in range(len(manifest["pages"]) // len(languages)):
            cycle = list(languages)
            rng.shuffle(cycle)
            assignments.extend(cycle)
        pairs = list(zip(manifest["pages"], assignments))
    else:
        pairs = [
            (page, language) for page in manifest["pages"] for language in languages
        ]
    cases = [
        {
            "id": f"{page['id']}-{language}",
            "page": page["id"],
            "language": language,
            "source_sha256": page["sha256"],
        }
        for page, language in pairs
    ]
    plan = {
        "kind": "actual Bodhan translation",
        "languages": args.languages,
        "deadline_utc": args.deadline_utc,
        "cases": cases,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "settings": {
            "layout_mode": "auto",
            "script": "native",
            "transliterate": False,
            "cache": False,
        },
        "source_sha256": {str(f): digest(f) for f in sorted(Path("app").rglob("*.py"))},
        "balanced_random_assignment": args.balanced,
        "seed": args.seed,
        "language_counts": dict(Counter(c["language"] for c in cases)),
    }
    if args.resume:
        previous = json.loads((root / "plan.json").read_text())
        if previous["cases"] != cases:
            p.error("Resume must preserve the original source/language assignments")
        plan = previous
        plan["resumed_utc"] = datetime.now(timezone.utc).isoformat()
        deadline = datetime.fromisoformat(plan["deadline_utc"]).timestamp()
    write_json(root / "plan.json", plan)
    snapshot = root / "code-snapshot"
    if not snapshot.exists():
        snapshot.mkdir()
        for name in ("app", "benchmarks"):
            shutil.copytree(
                name,
                snapshot / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
    for name, expected in plan["source_sha256"].items():
        if digest(snapshot / name) != expected:
            p.error("Frozen application hash mismatch: " + name)
    env = os.environ.copy()
    settings = Settings()
    env.update(
        BODHAN_API_KEY=settings.bodhan_api_key,
        BODHAN_OCR_API_KEY=settings.bodhan_ocr_api_key,
        STUDIO_FONT_DIR=str(settings.studio_font_dir.resolve())
        if settings.studio_font_dir
        else "",
        PYTHONPATH=str(snapshot),
    )
    for key in list(env):
        if key.startswith("STUDIO_") and key != "STUDIO_FONT_DIR":
            env.pop(key)
    queue = []
    for case in cases:
        directory = root / case["id"]
        path = directory / "result.json"
        if path.exists() and json.loads(path.read_text()).get("status") not in {
            "running",
            "interrupted",
        }:
            continue
        if directory.exists():
            directory.rename(
                root / (case["id"] + "-interrupted-" + str(time.time_ns()))
            )
        queue.append(case)
    (root / "STOP").unlink(missing_ok=True)
    active = {}
    stopped = []
    reason = "running"

    def stop(signum, frame):
        stopped.append("signal_" + str(signum))

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    report(root, plan)
    try:
        while queue or active:
            reason = (
                "deadline"
                if time.time() >= deadline
                else "stop_file"
                if (root / "STOP").exists()
                else stopped[0]
                if stopped
                else "running"
            )
            if reason != "running":
                break
            while queue and len(active) < args.workers:
                case = queue.pop(0)
                log = (root / (case["id"] + ".log")).open("a")
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        "-m",
                        "benchmarks.baseline.live",
                        "--out",
                        str(root),
                        "--case",
                        case["id"],
                    ],
                    cwd=snapshot,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                active[proc.pid] = (proc, case, log)
                print("Started " + case["id"], flush=True)
            for pid, (proc, case, log) in list(active.items()):
                if proc.poll() is None:
                    continue
                log.close()
                active.pop(pid)
                result = root / case["id"] / "result.json"
                if not result.exists():
                    result.parent.mkdir(exist_ok=True)
                    write_json(
                        result,
                        {
                            "case": case,
                            "status": "error",
                            "error": f"Worker exited {proc.returncode}; see log",
                        },
                    )
                print(
                    "Finished "
                    + case["id"]
                    + ": "
                    + json.loads(result.read_text())["status"],
                    flush=True,
                )
                report(root, plan)
            time.sleep(0.5)
        if reason == "running":
            reason = "completed"
    finally:
        for proc, case, log in active.values():
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        for proc, case, log in active.values():
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            log.close()
            result = root / case["id"] / "result.json"
            previous = (
                json.loads(result.read_text()) if result.exists() else {"case": case}
            )
            previous.update(status="interrupted", stop_reason=reason)
            result.parent.mkdir(exist_ok=True)
            write_json(result, previous)
        report(root, plan, reason)


if __name__ == "__main__":
    main()
