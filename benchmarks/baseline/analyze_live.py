"""Audit saved real-translation outputs without further model calls."""

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace as N

import pymupdf as fitz


def analyze(root):
    # Use the same algorithm definitions as the tested snapshot, even if the
    # workspace application has since changed.
    sys.path.insert(0, str(root / "code-snapshot"))
    from app.services.layout_tools import minimum_body_size
    from app.services.typography import neighbour_edges, penalty

    plan = json.loads((root / "plan.json").read_text())
    rows = []
    inventory = []
    for case in plan["cases"]:
        directory = root / case["id"]
        result = directory / "result.json"
        row = {
            "case": case["id"],
            "language": case["language"],
            "status": "not_started",
        }
        if not result.exists():
            rows.append(row)
            continue
        r = json.loads(result.read_text())
        row.update(status=r["status"], seconds=r.get("elapsed_seconds"))
        if (
            not (directory / "blocks.json").exists()
            or not (directory / "layout-report.json").exists()
        ):
            rows.append(row)
            continue
        blocks = {
            b["id"]: b for b in json.loads((directory / "blocks.json").read_text())
        }
        layout = json.loads((directory / "layout-report.json").read_text())
        measurements = []
        final_sizes = []
        shrink = []
        reasons = Counter()
        for page in layout["pages"]:
            before = []
            after = []
            owned = []
            for b in page["blocks"]:
                raw = blocks[b["block_id"]]
                reasons[b.get("failure_reason") or "rendered"] += 1
                if "before" not in b or not raw.get("translation"):
                    continue
                block = N(**raw)
                t = N(**raw["translation"])
                old = N(**b["before"])
                new = N(
                    bbox=b["bbox"], font_size=b["font_size"], fit_status=b["fit_status"]
                )
                before.append((block, t, old))
                after.append((block, t, new))
                owned.append(raw["id"])
                if new.fit_status == "fitted":
                    final_sizes.append(minimum_body_size(block, t, new.font_size))
                    shrink.append(new.font_size / raw["style"]["size"])
            edges = neighbour_edges(before)
            measurements.append(
                {
                    "page": page["page"],
                    "scope": "Independent fitting versus final accepted local passes, on the same pre-pass neighbor edges. Does not isolate typography from cell/paragraph flow.",
                    "before_penalty": penalty(before, edges),
                    "final_penalty_same_edges": penalty(after, edges),
                    "edges": [
                        {"a": owned[i], "b": owned[j], "weight": w} for i, j, w in edges
                    ],
                    "changed_cell_blocks": page["cell_changes"],
                    "changed_paragraph_blocks": page["paragraph_changes"],
                    "changes_accepted": page["change_accepted"],
                    "issue_counts_before": page["issue_counts_before"],
                    "issue_counts_after": page["issue_counts_after"],
                }
            )
        issues = json.loads((directory / "issues.json").read_text())
        inventory.extend(
            {"case": case["id"], "language": case["language"], **issue}
            for issue in issues
        )
        row.update(
            blocks=len(blocks),
            translation_retained_blocks=sum(
                b.get("translation_retained", False)
                for p in layout["pages"]
                for b in p["blocks"]
            ),
            minimum_fitted_body_size=min(final_sizes, default=None),
            below_6pt_body_blocks=sum(v < 5.95 for v in final_sizes),
            minimum_shrink_ratio=min(shrink, default=None),
            issue_count=len(issues),
            consistency_before=sum(m["before_penalty"] for m in measurements),
            consistency_final=sum(m["final_penalty_same_edges"] for m in measurements),
            failure_reasons=dict(reasons),
            issue_counts=dict(Counter(i["code"] for i in issues)),
        )
        artifact = directory / r["artifact"] if r.get("artifact") else None
        if artifact and artifact.exists():
            with (
                fitz.open(artifact) as output,
                fitz.open(root / "sources" / f"{case['page']}.pdf") as source,
            ):
                row["artifact_validation"] = {
                    "opens": True,
                    "page_count": len(output),
                    "dimensions_preserved": len(output) == len(source)
                    and all(a.rect == b.rect for a, b in zip(output, source)),
                    "sha256_matches": hashlib.sha256(artifact.read_bytes()).hexdigest()
                    == r.get("output_sha256"),
                }
        (directory / "baseline-metrics.json").write_text(
            json.dumps(
                {"metrics": row, "consistency": measurements},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        rows.append(row)
    summary = {
        "planned": len(rows),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "by_language": {
            lang: dict(Counter(r["status"] for r in rows if r["language"] == lang))
            for lang in plan["languages"]
        },
        "issue_counts": dict(Counter(i["code"] for i in inventory)),
        "case_metrics": rows,
        "limitations": [
            "One source page per test PDF, not full original multipage documents",
            "Automated checker findings require visual confirmation",
            "Language accuracy has not been independently evaluated",
            "Small samples per language do not establish language-level accuracy",
        ],
    }
    (root / "analysis.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    (root / "issue-inventory.json").write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False) + "\n"
    )
    fields = [
        "case",
        "language",
        "status",
        "seconds",
        "blocks",
        "translation_retained_blocks",
        "minimum_fitted_body_size",
        "below_6pt_body_blocks",
        "minimum_shrink_ratio",
        "issue_count",
        "consistency_before",
        "consistency_final",
    ]
    with (root / "case-metrics.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k not in {"case_metrics", "by_language"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    analyze(p.parse_args().root.resolve())
