# Default algorithm baseline

## Actual translation run

`live.py` runs actual Bodhan translations through the project upload, inspection,
translation and rendering APIs, using an isolated application/database per case.
For one target per source document, pass `--balanced`; with 100 inputs and 20
targets this assigns exactly five cases per language. The saved seed and shuffled
20-language cycles make assignment reproducible and spread language coverage
through the run. Arabic itself is not a supported provider target; `sd-Arab`,
`ur` and `ks` are supported Arabic-script targets.

```sh
.venv/bin/python -u -m benchmarks.baseline.live \
  --out output/benchmarks/live-baseline-20261029 \
  --source-suite output/benchmarks/baseline-20261029-1740/run \
  --languages hi sd-Arab ur ks ta te bn gu kn ml pa or as mr ne sa mni sat mai kok \
  --balanced --seed 20261029 --workers 4 \
  --deadline-utc 2026-10-29T13:06:15+00:00 --live
```

Use `--resume` with the same arguments to preserve completed/failed results and
restart only unfinished cases. Interrupted attempts are archived under separate
names. The original deadline and frozen application code are retained. A `STOP`
file in the live output directory terminates the active workers; in-flight
provider requests may still be processed. Explicit resume removes the STOP file.

The live output folder saves `plan.json`, source PDFs, `index.html`, `summary.json`,
per-worker logs and a code snapshot. Every case saves inspection and translation
job responses (including batch diagnostics/usage), translated blocks, issues,
document metadata, output PDF/draft, preview, layout report, XLIFF and isolated
database where available. `completed_with_issues` is not a visual-quality pass.
Interrupted cases keep their partial state and are explicitly labeled.

Analyze saved results without new calls:

```sh
.venv/bin/python -m benchmarks.baseline.analyze_live \
  output/benchmarks/live-baseline-20261029
```

This produces `analysis.json`, `issue-inventory.json`, `case-metrics.csv` and
per-case `baseline-metrics.json`, including same-edge consistency penalties
before all local layout passes versus their final accepted state. This metric
does not isolate typography from preceding cell/paragraph flow. Minimum body size
excludes intentional inline superscripts using the frozen algorithm definition.

The source suite contains **100 extracted single-page PDFs**, not all pages of
the original 100 source documents. All are public benchmark pages, not production
uploads. This run does not by itself validate multipage behavior or translation
accuracy; those require additional testing and bilingual review.

## Offline layout diagnostic

Runs the complete current `auto` layout pipeline on 100 real digital source pages,
using deterministic synthetic Latin expansion (`expand150`). It does not measure
translation accuracy, native-script shaping, OCR, multipage processing or live
provider reliability. It makes no provider calls and uses one isolated database
per case. Production documents and databases are not used.

## Selection

The existing 40 pinned DocLayNet pages plus one page from each of 60 distinct
locally downloaded ForMaT documents. ForMaT publishers are visited round-robin;
documents are ordered by stable IDs. For each document, inspect seven evenly
spaced pages, require at least 500 extracted characters and 85% ASCII letters,
and choose the page with the most spans (then characters). Selection uses no
renderer results. This is a convenience stress sample, not a representative
production sample. The manifest preserves original document hashes, URLs, page
indices, selection features and extracted-page hashes. Dataset rights continue
to apply; do not redistribute source PDFs without checking their terms.

## Run and stop

```sh
.venv/bin/python -u -m benchmarks.baseline.run \
  --out output/benchmarks/baseline-new --workers 3 --minutes 60

# Stop active workers while preserving complete results:
touch output/benchmarks/baseline-new/STOP

# Remove STOP before resuming; use the ORIGINAL absolute deadline to avoid
# extending a time-bounded experiment.
.venv/bin/python -u -m benchmarks.baseline.run \
  --out output/benchmarks/baseline-new --workers 3 --resume \
  --deadline-utc 2026-10-29T13:06:15+00:00
```

The parent polls the stop file/deadline every half second between reporting work;
active worker process groups receive termination and are killed if they do not
exit within two seconds each. This is an operating-system termination boundary,
not a safe checkpoint inside PyMuPDF. Incomplete case databases may need recovery
and must not be treated as completed results. SIGINT/SIGTERM also request a stop.
Deadline detection can be delayed briefly while reports are written. Preparation
precedes scheduling and is not individually interruptible by the stop-file poll.

Worker imports use a frozen `code-snapshot/`, so later workspace edits cannot mix
application versions within a run. Resume checks application hashes and refuses
changed application code. Completed results are reused; incomplete attempt
folders are renamed and preserved before a new attempt. Absolute font paths and
font hashes are recorded; do not modify font files during a run.

## Evidence

- `manifest.json`: all source cases and selection provenance.
- `provenance.json`: hashes, dependencies, default settings, deadline, run segments
  and interrupted cases. No secrets are recorded.
- `code-snapshot/`: the code used by subprocess workers.
- `sources/`: frozen input page PDFs and previews.
- `summary.json`, `baseline-summary.json`: case and aggregate diagnostics, including
  unfinished cases. `pass` means no checker finding, not human acceptance.
- `review.html`: evidence links and status filter.
- `index.html`: source/output previews for every completed case.
- Each case saves its final/draft PDF, preview, `result.json`, isolated SQLite,
  `structure.json`, XLIFF when applicable, and `layout-report.json`.
- `result.json` includes exact source/replacement text, source span ownership,
  original/final sizes, shrink ratios, bounding boxes, line breaks, fitting status,
  issue messages, retained-span counts, runtime and output hash validation.
- `layout-report.json` includes independent-fit snapshots, cell/paragraph changes,
  candidate acceptance/rollback, issue counts, translation hashes and actual
  checker-qualified retention outcomes. Fitted-span totals alone do not establish
  correct visible rendering.
- `consistency.json` records the weighted squared log-ratio penalty immediately
  before and after typography, then after acceptance/rollback on the SAME edges.
  It also stores neighbor pairs, weights, refit counts and changed block IDs.
  This is not a before/after measure of the preceding cell/paragraph passes.
- `worker-*.log`: per-case process output. Preserve the shell's redirected log too.

The generated text preserves protected literals across source style spans. Its
actual character growth is recorded; it is not always exactly 1.5 times longer.
Artificial repeated-character growth is a fitting stressor, not real linguistic
behavior. All checker findings remain hypotheses until visual verification;
inspect readability, table borders, images, styles and source-retaining regions.
No feature ablations or algorithm fixes are performed in this baseline.
