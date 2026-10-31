"""Read every saved case, classify evidence and create a focused review report.

Does not rerender, call a model, modify PDFs or overwrite the original results.
"""

import argparse
import html
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf as fitz

from app.agents.checker import normalized

DECISIONS = {
    "missing_text": (
        "Fixed two causes; inspect residuals",
        "Invisible placeholder suffix collisions are now avoided. Latin presentation ligatures compare with their expanded spelling; mathematical symbols remain distinct. Remaining local extraction mismatches are retained, not excused by finding the text elsewhere on the page.",
    ),
    "collision": (
        "Distinguish inherited geometry from new overlap",
        "Unchanged overlapping source/group boxes now produce a source_geometry_overlap warning. Newly introduced or increased overlap remains a collision error. This does not establish that inherited overlaps are visually harmless.",
    ),
    "source_geometry_overlap": (
        "Review original/group geometry",
        "Inspect visible text and source grouping. These boxes overlapped before fitting; font shrinking is not automatically a valid repair.",
    ),
    "small_text": (
        "Keep readability warnings; investigate local fit constraints",
        "Small text is separated into prose, numeric values and punctuation/symbols. Raw below-6pt totals also include text that was already small. No global minimum size or unsafe box expansion is imposed.",
    ),
    "font_size_inconsistent": (
        "Keep local typography diagnostics",
        "Compare relative shrinkage only within comparable roles and cells. Investigate readable words and table values separately from punctuation. The bounded typography pass is not a guarantee of equal final sizes.",
    ),
    "overflow": (
        "Preserve source; narrow marker fixtures remain open",
        "All 56 original overflow findings concern punctuation/symbol-only spans. Tight glyph boxes can be incompatible with replacement-font HTML line boxes. Keep the source and warning; do not enlarge boxes through neighbours to obtain a green result.",
    ),
    "unsupported_layout": (
        "Retain conservative source preservation",
        "All 54 original findings are rotated/vertical text. Rotation-aware layout is outside the current renderer. Preserving and reporting these regions is intentional.",
    ),
    "unsupported_glyphs": (
        "Separate source encoding from font coverage",
        "Control/private-use characters indicate suspect extraction or symbol mappings, not simply a missing Noto font. Other Unicode failures need font/glyph coverage inspection. No billable OCR fallback is enabled automatically.",
    ),
    "protected_value_changed": (
        "Correct synthetic fixture generation",
        "Protect complete line-level literals before expanding individual style spans. Restore whitespace-only spans to prevent email addresses swallowing adjacent prose. Actual protected-value changes still remain hard findings.",
    ),
    "graphics_changed": (
        "Retain graphics warnings; examine actual evidence",
        "Original pages 34 and 36 are the only affected pages. Page 34 retains the exact encoded JPEG and image placement; raster differences alone do not prove image replacement. Page 34 also has unresolved watermark opacity despite recovered font weight. Page 36 needs equation-region review. Do not weaken the pixel check globally.",
    ),
}


def role(text):
    return (
        "text"
        if any(c.isalpha() for c in text)
        else "numeric"
        if any(c.isdigit() for c in text)
        else "punctuation_or_symbol"
    )


def source_encoding(text):
    if any(unicodedata.category(c) == "Cc" and c not in "\r\n\t" for c in text):
        return "control_characters"
    if any(unicodedata.category(c) == "Co" for c in text):
        return "private_use_glyphs"
    if "\ufffd" in text:
        return "replacement_character"
    return "other_font_coverage"


def inventory(root):
    categories = defaultdict(
        lambda: {
            "count": 0,
            "pages": set(),
            "cases": set(),
            "classifications": Counter(),
            "examples": [],
        }
    )
    results = sorted(root.glob("*/result.json"))
    for path in results:
        data = json.loads(path.read_text())
        if data.get("status") == "error":
            continue
        rows = {r["id"]: r for r in data["layouts"]}
        missing = [i for i in data["issues"] if i["code"] == "missing_text"]
        pdf = fitz.open(path.parent / data["artifact"]) if missing else None
        output_page = pdf[0] if pdf else None
        textpage = output_page.get_textpage() if output_page else None
        try:
            for issue in data["issues"]:
                code = issue["code"]
                item = categories[code]
                item["count"] += 1
                item["pages"].add(path.parent.name[:2])
                item["cases"].add(path.parent.name)
                row = rows.get(issue["block_id"], {})
                text = row.get("source", "")
                classification = "unclassified"
                if code == "unsupported_glyphs":
                    classification = source_encoding(text)
                elif code == "unsupported_layout":
                    classification = (
                        "rotated_or_vertical"
                        if row["style"].get("vertical")
                        else "other"
                    )
                elif code in {"small_text", "font_size_inconsistent", "overflow"}:
                    classification = role(text)
                elif code == "missing_text":
                    actual = output_page.get_textbox(
                        fitz.Rect(row["bbox"]) + (-2, -2, 2, 2), textpage=textpage
                    )
                    if normalized(row["replacement"]) in normalized(actual):
                        classification = "equivalent_after_ligature_folding"
                    elif actual.strip() == "x" and row["replacement"].rstrip().endswith(
                        "x"
                    ):
                        classification = "placeholder_suffix_collision"
                    else:
                        classification = "remaining_local_extraction_mismatch"
                elif code == "protected_value_changed":
                    classification = (
                        "synthetic_expansion"
                        if "-expand150-" in path.parent.name
                        else "identity"
                    )
                item["classifications"][classification] += 1
                if len(item["examples"]) < 6:
                    item["examples"].append(
                        {
                            "case": path.parent.name,
                            "block_id": issue["block_id"],
                            "source": text[:180],
                            "classification": classification,
                            "message": issue["message"],
                        }
                    )
        finally:
            if pdf:
                pdf.close()
    return {
        code: {
            **item,
            "pages": sorted(item["pages"]),
            "cases": sorted(item["cases"]),
            "classifications": dict(item["classifications"]),
        }
        for code, item in sorted(categories.items())
    }


def write_review(baseline_root, current_root):
    old_summary = json.loads((baseline_root / "summary.json").read_text())
    new_summary = json.loads((current_root / "summary.json").read_text())
    expected = (
        new_summary["provenance"]["page_count"]
        * len(new_summary["provenance"]["modes"])
        * len(new_summary["provenance"]["profiles"])
    )
    if len(new_summary["cases"]) != expected:
        raise ValueError("Wait for the complete run before generating final review")
    old, new = inventory(baseline_root), inventory(current_root)
    regression_path = current_root / "regressions.json"
    regressions = (
        json.loads(regression_path.read_text()) if regression_path.exists() else []
    )
    acceptance_path = current_root / "acceptance.json"
    acceptance = (
        json.loads(acceptance_path.read_text()) if acceptance_path.exists() else {}
    )
    audit = {
        "baseline": str(baseline_root.resolve()),
        "current": str(current_root.resolve()),
        "baseline_fixture_version": old_summary["provenance"].get("fixture_version", 1),
        "current_fixture_version": new_summary["provenance"].get("fixture_version", 1),
        "caution": "Counts include changed diagnostics and corrected synthetic fixtures; reductions are not all rendering improvements.",
        "decisions": {
            k: {"decision": v[0], "reason": v[1]} for k, v in DECISIONS.items()
        },
        "baseline_inventory": old,
        "current_inventory": new,
        "regressions": regressions,
        "acceptance": acceptance,
    }
    (current_root / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )
    sections = []
    for code in sorted(set(old) | set(new)):
        title, reason = DECISIONS.get(
            code, ("Needs investigation", "No disposition recorded")
        )
        before, after = old.get(code, {}), new.get(code, {})
        samples = after.get("examples", before.get("examples", []))
        sample_root = current_root if after.get("examples") else baseline_root
        links = []
        # Both reports are siblings under output/benchmarks.
        for example in samples:
            prefix = "../" + sample_root.name + "/" + example["case"]
            links.append(
                '<li><a href="'
                + prefix
                + '/result.json">'
                + html.escape(example["case"])
                + "</a> — "
                + html.escape(str(example["block_id"]))
                + "<br><small>"
                + html.escape(example["source"])
                + "</small></li>"
            )
        sections.append(
            '<section id="'
            + code
            + '"><h2>'
            + html.escape(code)
            + "</h2><p><strong>"
            + html.escape(title)
            + "</strong></p><p>"
            + html.escape(reason)
            + "</p><p>Findings: "
            + str(before.get("count", 0))
            + " → "
            + str(after.get("count", 0))
            + "; pages in current run: "
            + str(len(after.get("pages", [])))
            + "</p><pre>"
            + html.escape(json.dumps(after.get("classifications", {}), indent=2))
            + "</pre><ul>"
            + "".join(links)
            + "</ul></section>"
        )
    regression_summary = (
        (
            "<p><strong>"
            + str(len(regressions))
            + " case/metric increases remain flagged.</strong> Restored spaces add real text width; "
            'the comparison is not an all-clear. <a href="regressions.json">Exact cases and metrics</a>. '
            "The narrow fixed boxes still need local fitting work.</p>"
        )
        if regressions
        else ""
    )
    rows = []
    before_cases = {
        (r["page"], r["profile"], r["mode"]): r for r in old_summary["cases"]
    }
    for row in new_summary["cases"]:
        key = (row["page"], row["profile"], row["mode"])
        before = before_cases[key]
        folder = row["folder"]
        all_codes = " ".join(row.get("issue_counts", {}))
        issues = html.escape(json.dumps(row.get("issue_counts", {})))
        attrs = f'data-mode="{row["mode"]}" data-profile="{row["profile"]}" data-codes="{all_codes}"'
        rows.append(
            f"<tr {attrs}><td>{row['page']}<br>{row['mode']} / {row['profile']}</td><td>"
            + f"{row['status']}<br>Retained source spans: {before.get('retained_source_spans', '?')} → {row.get('retained_source_spans', '?')}<br>"
            + f'<small>{issues}</small><br><a href="{folder}/result.json">Block details</a></td>'
            + f'<td><a href="sources/{row["page"]}.pdf"><img loading="lazy" src="sources/{row["page"]}.jpg"></a></td>'
            + f'<td><a href="{folder}/{row.get("artifact", "final.pdf")}"><img loading="lazy" src="{folder}/preview.jpg"></a></td></tr>'
        )
    options = "".join("<option>" + code + "</option>" for code in sorted(new))
    page = (
        """<!doctype html><meta charset="utf-8"><title>DocLayNet findings and fixes</title>
<style>body{font:16px system-ui;max-width:1500px;margin:32px auto;padding:0 20px;background:#f7f8fb;color:#192336}section{border:1px solid #d4dbe6;background:white;padding:20px;margin:18px 0}small{overflow-wrap:anywhere}table{width:100%;border-collapse:collapse}td,th{border:1px solid #cdd5df;padding:10px;vertical-align:top}td:nth-child(1){width:12%}td:nth-child(2){width:22%}img{width:100%;max-width:430px}select{padding:8px;margin:10px}a{color:#1755af}</style>
<h1>DocLayNet: findings, fixes and remaining limits</h1>
<p>240 cases across 40 pages. No model calls. The original run is preserved. Lower issue counts can reflect corrected diagnostics or fixtures; they do not automatically mean better visible rendering.</p>
<p>Confirmed fixes: whitespace-only source spans are recovered; searchable text avoids placeholder suffix collisions; Latin ligatures are compared correctly; explicit font-weight suffixes recover missing bold flags.</p>
<p>Open issues: tiny punctuation and rigid box fitting, unreadable source text layers, rotated text, unavailable glyphs, complex equation reconstruction and graphics warnings. No unsafe expansion, forced font floor or automatic OCR was introduced.</p>
<p><a href="audit.json">Complete issue inventory</a> · <a href="index.html">Full current report</a> · <a href="../__BASELINE__/index.html">Original baseline report</a></p>
"""
        + regression_summary
        + "".join(sections)
        + """<h2>Inspect individual pages</h2>
<label>Mode <select id="mode"><option value="">All</option><option>auto</option><option>paragraphs</option><option>spans</option></select></label>
<label>Text <select id="profile"><option value="">All</option><option>identity</option><option>expand150</option></select></label>
<label>Finding <select id="code"><option value="">All</option>"""
        + options
        + """</select></label>
<p id="count"></p><table><thead><tr><th>Case</th><th>Diagnostics</th><th>Original</th><th>Current output</th></tr></thead><tbody id="cases">"""
        + "".join(rows)
        + """</tbody></table>
<script>const mode=document.getElementById('mode'),profile=document.getElementById('profile'),code=document.getElementById('code');function filter(){let n=0;for(const r of document.querySelectorAll('#cases tr')){const ok=(!mode.value||r.dataset.mode===mode.value)&&(!profile.value||r.dataset.profile===profile.value)&&(!code.value||r.dataset.codes.split(' ').includes(code.value));r.hidden=!ok;if(ok)n++}document.getElementById('count').textContent=n+' cases shown'}for(const id of ['mode','profile','code'])document.getElementById(id).addEventListener('change',filter);filter()</script>"""
    )
    (current_root / "review.html").write_text(
        page.replace("__BASELINE__", html.escape(baseline_root.name, quote=True))
    )
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, default=Path("output/benchmarks/doclaynet40")
    )
    parser.add_argument(
        "--current", type=Path, default=Path("output/benchmarks/doclaynet40-reviewed")
    )
    args = parser.parse_args()
    audit = write_review(args.baseline, args.current)
    print(
        json.dumps(
            {
                code: {
                    "before": audit["baseline_inventory"].get(code, {}).get("count", 0),
                    "after": audit["current_inventory"].get(code, {}).get("count", 0),
                }
                for code in audit["decisions"]
            },
            indent=2,
        )
    )
