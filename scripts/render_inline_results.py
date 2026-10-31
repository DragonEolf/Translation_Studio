"""Render the saved synthetic test responses, including explicit rejected cases.
Uses no API and reads no private documents.
"""

import json
import xml.etree.ElementTree as ET

import pymupdf as fitz

from app.agents.translator import validate_literals
from app.config import Settings
from app.services.text_fitter import FontRegistry
from scripts.inline_layout_experiment import OUT, fit_inline, paint, synthetic, validate


def main():
    records = json.loads((OUT / "synthetic-api-results.json").read_text())
    fonts = FontRegistry(Settings().studio_font_dir)
    gs = synthetic()
    report = []
    with fitz.open() as doc:
        for rec in records:
            lang = rec["language"]
            page = doc.new_page(width=620, height=760)
            page.insert_text(
                (25, 25), "Nested IDs + inline formatting: " + lang, fontsize=16
            )
            paras = {
                p.get("id"): ET.tostring(p, encoding="unicode")
                for p in ET.fromstring("<batch>" + rec["output"] + "</batch>")
            }
            checks = []
            for i, g in enumerate(gs):
                y = 55 + i * 110
                page.insert_text(
                    (25, y), "".join(s["text"] for s in g["spans"]), fontsize=9
                )
                check = {"id": g["id"]}
                try:
                    runs = validate(paras.get(g["id"], ""), [g])[g["id"]]
                    check["structurally_valid"] = True
                    warnings = []
                    for original in g["spans"]:
                        target = next(s for s in runs if s["id"] == original["id"])
                        try:
                            validate_literals(original["text"], target["text"])
                        except Exception:
                            warnings.append(original["id"])
                    check["literal_style_anchor_failures"] = warnings
                    if warnings:
                        page.insert_text(
                            (25, y + 12),
                            "WARNING: protected value moved to a different formatting ID",
                            fontsize=8,
                            color=(0.8, 0, 0),
                        )
                    rect = fitz.Rect(25, y + 22, 590, y + 88)
                    size = fit_inline(runs, rect, 14, fonts, lang, 620, 760)
                    paint(page, runs, rect, size, fonts, lang)
                    check["font_size"] = size
                except Exception as e:
                    check.update(structurally_valid=False, error=str(e))
                    page.insert_text(
                        (25, y + 30),
                        "REJECTED: missing or invalid span IDs",
                        fontsize=12,
                        color=(0.8, 0, 0),
                    )
                checks.append(check)
            report.append({"language": lang, "paragraph_checks": checks})
        doc.save(OUT / "inline-formatting-demo.pdf")
    (OUT / "synthetic-validation-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
