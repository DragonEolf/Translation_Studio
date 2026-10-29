"""CLI: generate paired PDFs, translate with Studio, compare with a smaller Qwen."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.bodhan import LANGUAGE_NAMES

from .cases import plan
from .generate import generate_case, write_json, reference_images
from .client import OpenRouter
from .compare import Rubric, compare, fingerprint
from .prompts import GENERATOR, JUDGE
from .render import font_paths

GENERATOR_MODEL = "qwen/qwen3-vl-30b-a3b-instruct"
COMPARER_MODEL = "qwen/qwen3-vl-8b-instruct"
DEFAULT_RUBRIC = Path(__file__).with_name("rubric.json")


def wait_job(client, response, timeout):
    if response.status_code != 202:
        raise RuntimeError(f"Studio rejected job (HTTP {response.status_code})")
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/v1/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] in {"completed", "completed_with_issues"}:
            return job
        if job["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"Studio job {job['status']}: {job.get('error_code')}")
        time.sleep(0.2)
    client.post(f"/v1/jobs/{job_id}/cancel")
    raise RuntimeError("Studio job timed out and cancellation was requested")


def translate_case(case, directory, settings, timeout, layout_mode):
    started = time.monotonic()
    candidate = directory / "candidate.pdf"
    metadata = directory / "translation.json"
    import hashlib
    source_hash = hashlib.sha256((directory / "source.pdf").read_bytes()).hexdigest()
    if candidate.exists() and metadata.exists():
        previous = json.loads(metadata.read_text())
        if previous.get("source_sha256") == source_hash and previous.get("layout_mode") == layout_mode:
            return previous
    # Each attempt has its own database, so resumes never collide with a timed-out job.
    isolated = settings.model_copy(update={
        "studio_data_dir": directory / "studio" / uuid.uuid4().hex,
        "studio_workers": 1, "studio_vision_review_enabled": False})
    with TestClient(create_app(isolated)) as client:
        response = client.post("/v1/documents", files={
            "file": ("source.pdf", (directory / "source.pdf").read_bytes(), "application/pdf")})
        inspection = wait_job(client, response, timeout)
        document = response.json()["document_id"]
        translation = wait_job(client, client.post(f"/v1/documents/{document}/translations", json={
            "target_language": case["language"], "layout_mode": layout_mode}), timeout)
        response = client.get(f"/v1/documents/{document}/artifacts/final.pdf")
        response.raise_for_status()
        candidate.write_bytes(response.content)
        result = dict(document_id=document, inspection_status=inspection["status"],
                      elapsed_seconds=round(time.monotonic()-started, 3),
                      translation_status=translation["status"], source_sha256=source_hash,
                      layout_mode=layout_mode,
                      issues=client.get(f"/v1/documents/{document}/issues").json())
        write_json(metadata, result)
        return result


def evaluate_case(router, case, directory, rubric, model):
    paths = [directory / name for name in ("source.pdf", "reference.pdf", "candidate.pdf")]
    expected = fingerprint(paths, model, rubric)
    evaluation = directory / "evaluation.json"
    if evaluation.exists():
        saved = json.loads(evaluation.read_text())
        if saved.get("fingerprint") == expected and (directory / "annotated.pdf").exists():
            return saved
    return compare(router, *paths, case["language"], rubric, model, directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["generate", "translate", "compare", "run"])
    parser.add_argument("--count", type=int, default=10, help="TOTAL pairs; divisible by language count")
    parser.add_argument("--languages", nargs="+", default=["bn"], choices=sorted(LANGUAGE_NAMES))
    parser.add_argument("--output", type=Path, default=Path("output/pdf/openrouter-html-benchmark"))
    parser.add_argument("--generator-model", default=GENERATOR_MODEL)
    parser.add_argument("--comparer-model", default=COMPARER_MODEL)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--font-dir", type=Path)
    parser.add_argument("--reference-pdf", type=Path, action="append", default=[], help="Visual complexity reference; repeat for multiple PDFs")
    parser.add_argument("--execution-timeout", type=float, default=120, help="Overall HTML rendering deadline, in seconds")
    parser.add_argument("--api-timeout", type=float, default=180, help="Overall deadline for each model call, in seconds")
    parser.add_argument("--seed", type=int, default=42, help="Prompt variation seed; not a determinism guarantee")
    parser.add_argument("--job-timeout", type=float, default=600)
    parser.add_argument("--layout-mode", choices=["spans", "paragraphs", "auto"], default="auto")
    parser.add_argument("--live", action="store_true", help="Required: sends synthetic documents to paid APIs")
    parser.add_argument("--dry-run", action="store_true", help="Print balanced case plan without API calls or files")
    args = parser.parse_args(argv)
    try:
        cases = plan(args.count, args.languages)
        rubric = Rubric.model_validate_json(args.rubric.read_text())
        if args.job_timeout <= 0 or args.execution_timeout <= 0 or args.api_timeout <= 0:
            raise ValueError("Timeouts must be positive")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.dry_run:
        print(json.dumps({"count": len(cases), "per_language": dict(Counter(c["language"] for c in cases)),
                          "generator": args.generator_model, "comparer": args.comparer_model,
                          "cases": [{"id": c["id"], "theme": c["theme"]} for c in cases]}, indent=2))
        return 0
    if not args.live:
        parser.error("Use --live to enable paid API calls, or --dry-run to inspect the plan")
    settings = Settings()
    if args.font_dir:
        settings = settings.model_copy(update={"studio_font_dir": args.font_dir.resolve()})
    if args.command in {"translate", "run"} and not settings.bodhan_api_key:
        parser.error("Set BODHAN_API_KEY for Studio translation")
    try:
        router = OpenRouter(settings.openrouter_api_key, deadline=args.api_timeout) if args.command != "translate" else None
        fonts = font_paths(settings.studio_font_dir, "en")
        for language in args.languages:
            fonts.update(font_paths(settings.studio_font_dir, language))
    except ValueError as exc:
        parser.error(str(exc))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    _, reference_metadata = reference_images(args.reference_pdf)
    config = dict(pipeline="one-shot-html-v1", references=reference_metadata, languages=args.languages, seed=args.seed, generator_model=args.generator_model,
                  generator_prompt=GENERATOR, font_dir=str(settings.studio_font_dir))
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["config"] != config:
            parser.error("Output directory has a different generator configuration; choose a new directory")
        if len(manifest["cases"]) > len(cases):
            parser.error("Count is smaller than the existing suite; use its full count or a new directory")
    else:
        manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), config=config)
    manifest["cases"] = cases
    write_json(manifest_path, manifest)
    write_json(output / "prompts.json", dict(generator=GENERATOR, comparer=JUDGE))
    rows = []
    for case in cases:
        print(f"{case['id']}: {args.command} starting", flush=True)
        directory = output / case["id"]
        row = dict(case_id=case["id"], language=case["language"], status="failed")
        stage = "generation" if args.command in {"generate", "run"} else "case_validation"
        try:
            if args.command in {"generate", "run"}:
                generate_case(router, case, directory, fonts, args.generator_model, args.seed,
                              references=args.reference_pdf, timeout=args.execution_timeout)
            else:
                if not (directory / "case.json").exists():
                    raise ValueError("Generate the case first")
                saved = json.loads((directory / "case.json").read_text())
                if saved.get("status") != "completed" or saved["config"]["case"] != case:
                    raise ValueError("Saved case does not match suite plan")
            if args.command in {"translate", "run"}:
                stage = "translation"
                print(f"{case['id']}: translation starting", flush=True)
                translate_case(case, directory, settings, args.job_timeout, args.layout_mode)
            if args.command in {"compare", "run"}:
                stage = "comparison"
                print(f"{case['id']}: comparison starting", flush=True)
                result = evaluate_case(router, case, directory, rubric, args.comparer_model)
                row.update(score=result["score"], issues=result["issues"])
            row["status"] = "completed"
        except Exception as exc:
            # Provider HTTP bodies and keys never appear in logs.
            row["failed_stage"] = stage
            row["error"] = str(exc) if isinstance(exc, (ValueError, RuntimeError, FileNotFoundError)) else type(exc).__name__
        rows.append(row)
        write_json(output / f"{args.command}-results.json", rows)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return 1 if any(r["status"] == "failed" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
