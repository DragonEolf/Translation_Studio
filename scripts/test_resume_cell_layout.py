"""Local-only cell typography verification using existing Tamil translations.

Copies the test database consistently, keeps prior outputs untouched, and uses
a provider that raises on any translation call.
"""

import json
import shutil
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import Block, Issue, LayoutResult


class NoTranslation:
    calls = 0

    async def translate(self, *args):
        self.calls += 1
        raise AssertionError("This is a render-only test; no translation calls allowed")


def main():
    previous = Path("output/pdf/resume-live-paragraph-test")
    prior = json.loads((previous / "results.json").read_text())
    did = prior["document_id"]
    out = Path("output/pdf/resume-cell-layout-test")
    root = out / "run-data"
    root.mkdir(parents=True, exist_ok=True)
    (root / did).mkdir(exist_ok=True)
    with (
        sqlite3.connect(
            f"file:{previous / 'run-data' / 'studio.sqlite3'}?mode=ro", uri=True
        ) as src,
        sqlite3.connect(root / "studio.sqlite3") as dst,
    ):
        src.backup(dst)
    shutil.copyfile(
        previous / "run-data" / did / "source.pdf", root / did / "source.pdf"
    )
    settings = Settings()
    provider = NoTranslation()
    app = create_app(
        Settings(
            studio_data_dir=root,
            studio_font_dir=settings.studio_font_dir,
            studio_cache_enabled=False,
        ),
        provider,
    )
    with app.state.sessions() as s:
        before = {
            b.id: {
                "size": s.get(LayoutResult, b.id).font_size,
                "bbox": s.get(LayoutResult, b.id).bbox,
            }
            for b in s.scalars(select(Block).where(Block.document_id == did))
            if not b.style.get("retired") and s.get(LayoutResult, b.id)
        }
    start = time.perf_counter()
    app.state.orchestrator.render(did)
    with app.state.sessions() as s:
        blocks = [
            b
            for b in s.scalars(select(Block).where(Block.document_id == did))
            if not b.style.get("retired")
        ]
        cells = defaultdict(list)
        records = []
        for b in blocks:
            entry_layout = s.get(LayoutResult, b.id)
            if not entry_layout:
                continue
            record = {
                "id": b.id,
                "source": b.source_text,
                "source_size": b.style["size"],
                "size_before": before[b.id]["size"],
                "size_after": entry_layout.font_size,
                "bbox_before": before[b.id]["bbox"],
                "bbox_after": entry_layout.bbox,
                "fit_status": entry_layout.fit_status,
                "cell": b.style.get("table_cell_bbox"),
            }
            records.append(record)
            if record["cell"]:
                cells[tuple(record["cell"])].append(record)
        inconsistent = []
        shared = []
        for cell, rs in cells.items():
            if len(rs) < 2:
                continue
            by_size = defaultdict(list)
            for r in rs:
                by_size[round(r["source_size"], 2)].append(r)
            for size, group in by_size.items():
                if len(group) < 2:
                    continue
                old = [r["size_before"] for r in group]
                new = [r["size_after"] for r in group]
                shared.append(
                    {"cell": cell, "source_size": size, "before": old, "after": new}
                )
                if max(new) - min(new) > 0.01:
                    inconsistent.append(shared[-1])
        issues = [
            {"code": i.code, "block_id": i.block_id, "severity": i.severity}
            for i in s.scalars(
                select(Issue).where(
                    Issue.document_id == did, Issue.resolution != "resolved"
                )
            )
        ]
    result = {
        "seconds": round(time.perf_counter() - start, 3),
        "translation_calls": provider.calls,
        "paragraphs": len(records),
        "rendered": sum(r["fit_status"] == "fitted" for r in records),
        "checked_equal_source_size_groups": len(shared),
        "remaining_inconsistent_groups": inconsistent,
        "issue_counts": dict(Counter(i["code"] for i in issues)),
        "groups": shared,
        "layouts": records,
        "issues": issues,
    }
    out.mkdir(exist_ok=True)
    dest = out / "Resume - Tamil - consistent cell sizes.pdf"
    shutil.copyfile(root / did / "final.pdf", dest)
    (out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in {"groups", "layouts", "issues"}
            },
            indent=2,
        )
    )
    print(
        "Autonomous Robotics body:",
        [
            {
                "source": r["source"][:70],
                "before": r["size_before"],
                "after": r["size_after"],
            }
            for r in records
            if "PD control" in r["source"]
            or "Kalman" in r["source"]
            or "Landmark" in r["source"]
            or "perception pipeline" in r["source"]
        ],
    )


if __name__ == "__main__":
    main()
