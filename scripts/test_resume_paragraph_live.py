"""Explicitly requested live resume test. Sends the specified PDF text to Bodhan.

Uses an isolated output database, never modifies the source or production jobs.
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from statistics import median

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import LayoutResult
from app.providers.bodhan import BodhanProvider


class MeasuredProvider(BodhanProvider):
    def __init__(self, key, log):
        super().__init__(key)
        self.log = log

    async def translate(self, text, language, script):
        record = {
            "input_chars": len(text),
            "kind": "paragraph_batch" if text.startswith("<p") else "span_fallback",
            "status": "running",
        }
        self.log.append(record)
        start = time.perf_counter()
        try:
            result = await super().translate(text, language, script)
            record.update(
                status="returned",
                seconds=round(time.perf_counter() - start, 3),
                truncated=result.get("truncated"),
                usage=result.get("usage", {}),
            )
            return result
        except Exception as exc:
            record.update(
                status="failed",
                seconds=round(time.perf_counter() - start, 3),
                error=getattr(exc, "code", type(exc).__name__),
            )
            raise
        finally:
            print(json.dumps({"provider_call": len(self.log), **record}), flush=True)


def wait(client, response, label):
    if response.status_code != 202:
        raise RuntimeError(response.text)
    deadline = time.monotonic() + 1200
    last = None
    last_print = 0
    while time.monotonic() < deadline:
        job = client.get("/v1/jobs/" + response.json()["job_id"]).json()
        state = (job["status"], round(job["progress"], 2))
        if state != last and (
            time.monotonic() - last_print > 5
            or job["status"] in {"completed", "completed_with_issues", "failed"}
        ):
            print(
                json.dumps(
                    {
                        "stage": label,
                        "status": job["status"],
                        "progress": job["progress"],
                        "stats": job["payload"].get("translation_batch_stats"),
                    }
                ),
                flush=True,
            )
            last = state
            last_print = time.monotonic()
        if job["status"] in {"completed", "completed_with_issues", "failed"}:
            return job
        time.sleep(0.25)
    raise RuntimeError("job timeout")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--language", default="ta")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--live", action="store_true", help="Enable paid provider calls")
    args = p.parse_args()
    if not args.live:
        p.error("--live is required for paid provider calls")
    args.output.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    calls = []
    provider = MeasuredProvider(settings.bodhan_api_key, calls)
    app = create_app(
        Settings(
            studio_data_dir=args.output / "run-data",
            studio_font_dir=settings.studio_font_dir,
            studio_cache_enabled=False,
        ),
        provider,
    )
    start = time.perf_counter()
    with TestClient(app) as client:
        upload = client.post(
            "/v1/documents",
            files={
                "file": (
                    args.source.name,
                    args.source.read_bytes(),
                )
            },
        )
        did = upload.json()["document_id"]
        inspection = wait(client, upload, "inspect")
        job = wait(
            client,
            client.post(
                f"/v1/documents/{did}/translations",
                json={"target_language": args.language, "layout_mode": "paragraphs"},
            ),
            "translate",
        )
        blocks = client.get(f"/v1/documents/{did}/blocks").json()
        issues = client.get(f"/v1/documents/{did}/issues").json()
        active_issues = [i for i in issues if i["resolution"] != "resolved"]
        artifacts = {}
        for name in ["final.pdf", "draft.pdf", "translation.xlf", "structure.json"]:
            r = client.get(f"/v1/documents/{did}/artifacts/{name}")
            if r.status_code == 200:
                dest = args.output / (
                    "Resume - Tamil - paragraphs.pdf" if name == "final.pdf" else name
                )
                dest.write_bytes(r.content)
                artifacts[name] = str(dest.resolve())
        with app.state.sessions() as s:
            layouts = {b["id"]: s.get(LayoutResult, b["id"]) for b in blocks}
            sizes = [
                entry_layout.font_size
                for entry_layout in layouts.values()
                if entry_layout and entry_layout.fit_status == "fitted"
            ]
            effective = [
                layouts[b["id"]].font_size * span["style"]["size"] / b["style"]["size"]
                for b in blocks
                if layouts[b["id"]] and layouts[b["id"]].fit_status == "fitted"
                for span in b["style"].get("rich_text", {}).get("source_spans", [])
            ]
        result = {
            "language": args.language,
            "source": str(args.source.resolve()),
            "document_id": did,
            "seconds": round(time.perf_counter() - start, 3),
            "inspection": inspection["status"],
            "job": job,
            "paragraphs": len(blocks),
            "source_spans": sum(
                len(b["style"].get("rich_text", {}).get("source_spans", []))
                for b in blocks
            ),
            "translated_paragraphs": sum(bool(b.get("translation")) for b in blocks),
            "rendered_paragraphs": len(sizes),
            "formatting_fallback_paragraphs": sum(
                bool(
                    b.get("translation")
                    and b["translation"]["metadata_json"]
                    .get("rich_text", {})
                    .get("fallback")
                )
                for b in blocks
            ),
            "logical_provider_calls": len(calls),
            "provider_calls": calls,
            "issue_counts": dict(Counter(i["code"] for i in active_issues)),
            "issues": active_issues,
            "font_sizes": sizes,
            "median_paragraph_pt": median(sizes) if sizes else None,
            "effective_span_median_pt": median(effective) if effective else None,
            "effective_spans_below_6pt": sum(x < 6 for x in effective),
            "artifacts": artifacts,
        }
        (args.output / "results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        (args.output / "blocks.json").write_text(
            json.dumps(blocks, ensure_ascii=False, indent=2)
        )
        print(
            json.dumps(
                {
                    k: v
                    for k, v in result.items()
                    if k not in {"job", "provider_calls", "issues", "font_sizes"}
                },
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
