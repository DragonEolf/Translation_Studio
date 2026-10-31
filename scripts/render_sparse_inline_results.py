"""Render saved synthetic sparse-tag results without API calls."""

import json

import pymupdf as fitz

from app.config import Settings
from app.services.text_fitter import FontRegistry
from scripts.inline_layout_experiment import OUT, fit_inline, paint, synthetic
from scripts.test_sparse_inline import validate_sparse


def main():
    records = json.loads((OUT / "sparse-api-results.json").read_text())
    fonts = FontRegistry(Settings().studio_font_dir)
    gs = synthetic()
    with fitz.open() as doc:
        for rec in records:
            if rec["tag"] != "b":
                continue
            parsed = validate_sparse(rec["output"], gs, rec["tag"])
            p = doc.new_page(width=620, height=760)
            p.insert_text(
                (25, 25),
                "Paragraph layout + inline formatting: " + rec["language"],
                fontsize=16,
            )
            for i, g in enumerate(gs):
                y = 55 + i * 110
                p.insert_text(
                    (25, y), "".join(s["text"] for s in g["spans"]), fontsize=9
                )
                rect = fitz.Rect(25, y + 12, 590, y + 88)
                runs = parsed[g["id"]]
                size = fit_inline(runs, rect, 14, fonts, rec["language"], 620, 760)
                paint(p, runs, rect, size, fonts, rec["language"])
        doc.save(OUT / "inline-formatting-demo.pdf")
    print("Rendered Hindi/Tamil sparse formatting demo from saved responses.")


if __name__ == "__main__":
    main()
