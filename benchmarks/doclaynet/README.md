# DocLayNet 40-page rendering suite

Evaluates Translation Studio's **digital PDF extraction, grouping, fitting,
reconstruction and local checks**. No translation quality score and no provider
calls. Production uploads, production SQLite and `.env` are not used.

## Dataset

40 original single-page PDFs from the official DocLayNet **test** split, covering
40 different source documents. The sample has all 11 annotation labels and six
document categories: financial reports (7), government tenders (7), laws and
regulations (7), manuals (7), patents (6), scientific articles (6).

The checked-in `manifest.json` pins page IDs, source document names, layout-label
counts, seed, PDF SHA-256 checksums and the official annotation checksum. Selection
round-robins categories, favors underrepresented layout labels within each category,
and excludes repeated annotation variants (`precedence > 0`). It never uses
renderer results to choose pages. This is a small diagnostic set, not a statistical
estimate of production quality. Keep a separate holdout if tuning against it.

Sources: [official repository](https://github.com/DS4SD/DocLayNet),
[paper](https://arxiv.org/abs/2206.01062). Original documents remain subject to the
dataset's published licensing terms; dataset binaries and generated artifacts are
kept in ignored directories.

## Prepare and run

From the repository root:

```bash
# Fetches just ZIP directories and the selected PDFs using HTTP byte ranges.
# Default selection uses the checked-in manifest; no full archive download.
.venv/bin/python -m benchmarks.doclaynet.prepare

# 240 cases: 40 pages × 3 modes × 2 profiles. No API keys required.
.venv/bin/python -m benchmarks.doclaynet.run --workers 4

# Small smoke run, preserving all prior output directories.
.venv/bin/python -m benchmarks.doclaynet.run --limit 1 --out output/benchmarks/smoke

# Optional Indic/RTL stress run (synthetic text, not real translations).
.venv/bin/python -m benchmarks.doclaynet.run --profiles hindi tamil urdu \
  --modes auto --fonts fonts --out output/benchmarks/doclaynet40-scripts

# Harness verification; synthetic fixtures, no dataset download required.
.venv/bin/python -m pytest -q tests/test_doclaynet_benchmark.py
```

An existing output directory is refused so previous results cannot be overwritten.
Use a new `--out` for each run. `--workers` uses separate processes, never shares
PyMuPDF objects across threads, and defaults to 2. Larger/dense pages take longer.
No font directory means the application's built-in Latin font path. Pass
`--fonts fonts` to evaluate the installed Noto selection used by a configured app;
font file hashes are recorded. Native script profiles require the relevant fonts.

## Profiles and fair comparisons

- `identity`: exact original span text. A baseline for damage caused by extraction,
  regrouping, font replacement and reconstruction alone.
- `expand150`: deterministic synthetic character expansion, adding `x` after
  approximately half the alphabetic characters. Protected numbers, codes, emails,
  URLs and whitespace are preserved. It is **not exactly 1.5×** for every block;
  the report records actual character growth. This stresses fitting with longer
  strings, including longer words. It is not a linguistic translation simulator.
- `hindi`, `tamil`, `urdu`: replace alphabetic characters with a fixed native-script
  character, retaining protected literals. These exercise script/font selection and
  basic RTL machinery. They do **not** validate realistic ligatures, combining
  marks, word shaping, mixed-script bidi or language quality. Real translated-text
  fixtures remain necessary for those cases.

Replacements are generated once per original source span. Every layout mode uses
those same replacements, including rich-text run style ownership; paragraph
reflow may normalize whitespace. Grouping must account for every source span
exactly once, or the case errors. Coverage is measured in original source spans,
so a mode cannot improve its score simply by combining spans into fewer blocks.
Font-size and issue counts remain block-based and must be interpreted with the
reported block counts; they are not directly comparable rates between modes.

## Outputs and interpretation

Open `output/benchmarks/doclaynet40/index.html` for source/output previews and PDF
links. `summary.json` records case summaries and mode/profile aggregates. Every
case includes its own `result.json` (block geometry, font sizes, source/replacement
text, styles and checker issues), searchable `final.pdf` or `draft.pdf`, preview,
and isolated SQLite database plus `structure.json` under `state/page/`.

Collected diagnostics:

- Rendered versus retained **source spans**; missing or duplicated grouping members.
- Page clipping, overlapping boxes, expected-text extraction failures, protected
  values, and changed pixels outside replacement regions, using the current checker.
- Minimum font size, shrink ratio, blocks below 6 pt, and typography warnings.
- Source/target character growth, timing and original/compacted output file sizes.
- Dataset/harness/git provenance and font configuration.

`pass` means no reported issues and complete span coverage. `review` means the
renderer produced output with issues or retained source. `error` means a harness or
rendering exception. Image-only and empty pages cannot silently pass. The command
exits 1 for errors; review findings are benchmark measurements, not harness errors.
There are deliberately no invented acceptance thresholds. Establish them after
reviewing the first baseline. All cases are attempted even if one case errors.

The checker is part of the system under test, not independent ground truth. A
searchable ActualText layer does not prove visible glyph correctness; source text
can also hide a failed replacement from text extraction. Style metadata confirms
intended formatting, not visible fidelity. Manually inspect a consistent subset
for bold/italic/underlines, hierarchy, table borders, image integrity and alignment.
Scanned OCR behavior is outside this digital-PDF suite.

The exported PDF is losslessly compacted (duplicate objects removed and streams
compressed) after the existing checker runs. Redundant generated PDF/PNG copies
are discarded to control disk use; the original source, database, geometry and
final compacted PDF remain. The renderer itself is not modified.

## Next: ForMaT with a different translation model

Use English source PDFs and a language supported by the renderer. The current
renderer routes native scripts through its Indic/Urdu language registry, so do
not assume ForMaT's German/French/etc. targets can be selected without an adapter.
English targets are already supported; Indic targets need external translated text.

Generate and save a fixed set of real translations with one chosen model, preserve
source span IDs and inline style IDs, then replay those saved texts through every
layout mode. Store model/version, prompt and translation fixture hashes. Evaluate
rendering and content coverage with this suite's measurements; do not use ForMaT
reference wording as a rendering success criterion. Model variation should be a
separate fixture set, rather than fresh calls on every rendering run.

This follow-up is a plan, not an implemented ForMaT importer or a model run.

## Regression checks after making renderer changes

Preserve this baseline, run the same matrix into another output directory, then:

```bash
.venv/bin/python -m benchmarks.doclaynet.compare \
  output/benchmarks/doclaynet40/summary.json \
  output/benchmarks/doclaynet40-next/summary.json
```

This exits nonzero for case errors or increases in retained source spans,
small-text blocks, hard checker findings or individual hard issue categories.
It also flags changed source-span counts. Comparison is per page/mode/profile;
one page's improvement cannot hide another page's regression. Incomplete runs or
changed dataset, modes, profiles or fonts are refused. Findings are written to
`regressions.json` alongside the current summary. Human review is still required
for visible quality and for checker false positives.

## Evidence review after the first baseline

`tests/test_doclaynet_regressions.py` contains reduced regressions for dropped
whitespace-only spans, formatted words that must stay joined, searchable
ActualText ending in its invisible placeholder, Latin ligature comparison,
inherited versus increased overlaps, explicit font-weight suffixes and literals
split across style spans.

The revised harness protects literals using complete source lines before
expanding individual spans (`fixture_version: 2`). It also records application
source hashes, text/numeric/punctuation small-text counts, and how many small
blocks fall below their original size or the 6 pt target. Legacy below-6pt
counts remain available for comparison. The native-script profiles are still
basic synthetic smoke inputs, not realistic language fixtures.

After a complete reviewed run, build the issue inventory and filtered review:

```bash
.venv/bin/python -m benchmarks.doclaynet.audit \
  --baseline output/benchmarks/doclaynet40 \
  --current output/benchmarks/doclaynet40-reviewed
```

This reads **every saved issue** and writes `audit.json` and `review.html` alongside
the current report. It does not rerender or call a model. The review explains the
decision for every issue category and lets you filter source/output pairs by
layout mode, text profile and finding.

Interpret before/after counts carefully: inherited overlap is now a separate
warning, Latin ligatures compare equivalently, and synthetic literal protection
was corrected. Those measurement/fixture changes must not be described as visual
rendering repairs. Actual visual fixes are reviewed separately against the PDFs.
