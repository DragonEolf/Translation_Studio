# One-shot PDF test generator and comparer

Each case makes **one Qwen call** for two complete HTML/CSS documents:
`source_html` (English) and `reference_html` (target language). Qwen controls all
content, page sizes, typography, columns, tables, diagrams and layout. The runner
supplies a document brief and fonts, with no boxes, text slots or page templates.

A fixed Playwright/Chrome renderer prints the unchanged HTML into `source.pdf`
and `reference.pdf`. It supplies embedded regular/bold Noto fonts under the `Doc`
font family. Model JavaScript and external resource requests are disabled. Inline
SVG is supported. No generated Python or PDF-library code is executed.

This is **one shot**: no visual critique, repair or regeneration loop. A failed
call or render remains a failed case. Generation disables HTTP retries and
provider fallbacks. Basic file checks only ensure both PDFs open. A hard 180-second
API deadline prevents indefinitely hanging requests.

Defaults: generator `qwen/qwen3-vl-30b-a3b-instruct`, comparer
`qwen/qwen3-vl-8b-instruct`. Models are configurable; the smaller model is not
necessarily cheaper at current provider prices.

## Run

From the repository root:

```sh
# Plan only; no files or API calls.
.venv/bin/python -m benchmarks.openrouter.run generate --count 10 --dry-run

# Generate 10 paired cases (20 PDFs), with no translation or comparison.
.venv/bin/python -m benchmarks.openrouter.run generate --count 10 --languages bn --live

# Generate, translate through Studio, then score each candidate PDF.
.venv/bin/python -m benchmarks.openrouter.run run --count 10 --languages bn --live

# Run individual stages against an existing suite.
.venv/bin/python -m benchmarks.openrouter.run translate --count 10 --languages bn --live
.venv/bin/python -m benchmarks.openrouter.run compare --count 10 --languages bn --live

# Equal language coverage: 20 cases per language.
.venv/bin/python -m benchmarks.openrouter.run generate --count 100 \
  --languages bn hi ta te gu --output output/pdf/multilingual-html-suite --live
```

`--count` is the total number of pairs and must divide evenly across languages.
Corresponding cases across languages receive the same document brief, with content
and layout generated independently. Ten complex document families include dense
resumes, manuals, financial reports, forms, datasheets and conference programmes.
Larger suites cycle families with fresh generated content.

Optional flags:

- `--generator-model ID`, `--comparer-model ID`.
- `--reference-pdf /path/to/example.pdf`, repeatable. Sends all pages of each
  supplied PDF as images to OpenRouter. Use references you intend to share with
  that service. At most four pages per reference; larger references are rejected.
- `--font-dir PATH`, otherwise `STUDIO_FONT_DIR` from `.env`.
- `--execution-timeout 120`, wall-clock seconds for HTML rendering.
- `--api-timeout 180`, total wall-clock deadline per model call, including retries.
- `--job-timeout 600`, seconds per Studio job.
- `--layout-mode auto` (`spans` and `paragraphs` also supported).
- `--rubric PATH`, custom scoring criteria whose maxima sum to 100.
- `--seed 42`, a variation hint, not a determinism guarantee.

Set `OPENROUTER_API_KEY` for generation/comparison and `BODHAN_API_KEY` for Studio
translation. Install the included regular/bold Noto fonts for all selected
languages. No normal user uploads or production database are processed.

## Rendering requirements

Install Node.js, Playwright and a Chromium browser. The runner also discovers the
Codex bundled Node/Playwright runtime when available. On macOS it uses installed
Google Chrome by default. Otherwise Playwright uses its bundled Chromium.

- `STUDIO_PDF_NODE`: optional explicit Node executable.
- `NODE_PATH`: optional node_modules directory containing Playwright.
- `CHROME_PATH`: optional browser executable override.

The trusted rendering script is `scripts/render_html_pair.cjs`. The renderer
is checked with a real Chrome launch before any generation call is charged.
Rendering requires permission to launch Chrome in restricted execution environments.
Browser contexts disable model-authored JavaScript, service workers and external resource requests.
The renderer inherits no API keys. Font assets are injected as CSS data URLs;
HTML source files are saved exactly as returned by Qwen. No layout/content changes
are made by the runner. Network access is used only for model/provider calls.

Each attempted case is recorded before its paid call. Completed cases are reused
without another call or execution. Failed or interrupted attempts are **not**
retried when resuming: use a new output directory for a new experiment. Increase
`--count` to append fresh cases to the same suite. Keep generator configuration,
languages, references, seed and font directory consistent. Earlier fixed-box and Python-code
suites are intentionally incompatible with this pipeline.

## Results

Default directory: `output/pdf/openrouter-html-benchmark`.

Each case saves:

- `case.json`: configuration, status, timings, usage, hashes and errors.
- `generation-response.json`: raw model response and reported usage/cost.
- `source.html`, `reference.html`: exact model-authored HTML.
- `source-fonts.css`, `reference-fonts.css`: injected local font assets.
- `render.log`: fixed browser renderer diagnostics.
- `source.pdf`, `reference.pdf`: generated pair, if successful.
- `candidate.pdf`: Studio's translated output, after translation.
- `evaluation.json`: rubric score, criterion scores and localized issues.
- `annotated.pdf`: candidate with issue rectangles and improvement comments.
- `comparison-response.json`: raw comparer output and reported usage/cost.

The suite directory saves the manifest, prompts and per-stage result files. The
CLI exits nonzero if any requested case fails and preserves partial results.
Failures include the stage that failed; the CLI prints translation and comparison
stage transitions while a case is running.

The comparer sees the actual PDFs as page images plus extracted text. It selects
measured candidate region IDs; code attaches their page number and bounding box.
Boxes are `[x0,y0,x1,y1]` in candidate PDF points, measured from the top-left;
pages start at 1. Text regions are preferred; coarse page-grid regions support
missing content. Each issue gets a rubric criterion and a one-line improvement
of at most 200 characters. Unknown regions/criteria and excessive points are
rejected. Overall score is computed in code from the criterion scores.

Edit `rubric.json` or pass `--rubric`. Default weights: meaning 40, completeness
20, layout 25, rendering 15. Changing PDFs, rubric, comparer prompt or model
invalidates the cached comparison. Comparisons accept at most twelve pages per
document; longer inputs are rejected rather than silently truncated.

The references are trusted AI output, and the judge is an automated estimate.
Neither has human validation. Valid alternative translations and reflow are
allowed; a smaller comparer may still miss defects or choose incorrect regions.

## Tests

```sh
.venv/bin/python -m pytest tests/test_openrouter_benchmark.py -q
```

Tests exercise single-call/single-render behavior, failure persistence, balanced
language allocation, actual browser rendering and rejection of active HTML, rubric validation,
annotations and actual PDF-image comparison inputs. No paid APIs are used.
