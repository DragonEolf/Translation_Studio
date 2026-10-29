# UGC English–Hindi page benchmark

Translates the **first 50 physical English PDF pages, including the cover and
front matter**, through Studio's actual upload/inspection/translation/rendering
pipeline. Each page is an independent case; this does not test multipage flow or
full-document context. The contiguous opening sample is a smoke/regression suite,
not representative coverage of the whole report.

Translation runs sequentially. As soon as page N finishes, one separate process
compares it through OpenRouter while Studio translates page N+1. At most two
comparisons are outstanding (one running, one queued); translation pauses if the
judge falls behind. PyMuPDF work is separated by process, not concurrent threads.
All comparisons finish before the command exits. Failed pages are recorded and
the remaining pages continue.

## Commands

From the repository root, inspect the plan without writing files or calling APIs:

```sh
.venv/bin/python -m benchmarks.ugc.run --dry-run
```

When ready, explicitly start paid translation and evaluation:

```sh
.venv/bin/python -u -m benchmarks.ugc.run --live
```

Defaults point to the two supplied PDFs in `~/Desktop/NitiAayogTest` and output
`output/pdf/ugc-first-50`. Override with `--english`, `--hindi`, `--output`,
`--start-page`, `--count`, `--font-dir`, `--layout-mode`, `--comparer-model`,
`--rubric`, `--job-timeout` or `--api-timeout`.

Requires `BODHAN_API_KEY`, `OPENROUTER_API_KEY` and configured Hindi fonts, loaded
from `.env` or environment. No synthetic generation call is made. English inputs
are sent through Studio's configured providers; only the official Hindi reference and
actual Hindi output page images/text are sent to OpenRouter for evaluation. Credentials
are not written to manifests.

## Hindi similarity scoring

The judge receives exactly two documents: the same-numbered official Hindi page
(ground truth) and Studio's actual Hindi output for the English page. No English
source, neighboring pages, reference context or alignment metadata is sent to the
judge. The first 50 pages are paired directly by physical page index.

Content similarity (50), layout similarity (35), and appearance similarity (15)
sum to 100. The score measures resemblance to the official Hindi page, including
wording, values, structure and visual presentation. It does not assess faithful
translation from English. Differences in the editions' pagination will therefore
also affect similarity scores. The judge uses rendered images to resolve legacy
font extraction artifacts. Each reported difference identifies a measured candidate
region and a short improvement. No passing threshold is assumed.

## Evidence and resume

Each `page-NNNN/` saves extracted `source.pdf`, same-numbered Hindi `reference.pdf`, input
hashes, isolated Studio data, `translation.json`, `candidate.pdf`, `status.json`,
`evaluation.json`, raw `comparison-response.json`, and `annotated.pdf` where the
relevant stage succeeds. `results.json` updates after each stage and reports
completed/failed counts, per-page criteria/issues and the completed-case mean.
Failures remain in the denominator counts; an unfinished suite is not a success.
`manifest.json` pins input hashes, page selection, code hashes, nonsecret settings,
model, prompt, rubric and timeouts. Original PDFs remain untouched.

Run the same command/output again to resume: completed translations and matching
comparisons are cached. Failed/interrupted attempts are skipped unless you add
`--retry-failed`, which may incur new provider charges. A changed configuration,
input or application/benchmark code requires a new output directory. Avoid editing
code or fonts during a run. This runner must have exclusive ownership of its output
directory; do not launch two copies against the same directory.

Ctrl-C stops submission; already running calls may finish while the executor
shuts down. Interrupted status is retained for explicit retry on the next run.
Provider retries use the existing clients' bounded retry policies.

## API diagnostics

Per-page `bodhan-requests.jsonl`, `ocr-requests.jsonl` (when OCR is used), and
`openrouter-requests.jsonl` record every attempted HTTP call, including recovered
retries. Categories distinguish HTTP 429 rate limits, 5xx provider server errors,
authentication failures, rejected requests, timeout exception types, connection
errors and invalid JSON. Numeric retry/limit headers and timings are saved;
credentials, request text and response bodies are excluded. The benchmark injects
recording transports; the application's existing retries and translation algorithm
are unchanged.

`results.json` aggregates request categories and HTTP statuses by service, as well
as terminal failure categories. `status.json` includes the original Studio job
error code and last request evidence on failure. Content/schema validation and
internal processing failures remain separate from network failures. A successful
HTTP request can still fail content validation. Unknown causes are left unclassified
rather than inferred to be rate limits. Diagnostics use fully buffered responses
and a fresh connection pool for each request attempt.

## Offline tests

These use temporary synthetic PDFs and fake judges; no paid calls:

```sh
.venv/bin/python -m pytest tests/test_ugc_benchmark.py tests/test_openrouter_benchmark.py -q
```
