# Release verification — October 29, 2026

Verification used a temporary copy of the release files, with no production
`.env`, database, uploaded documents or generated outputs copied into it. A fresh
Python 3.13 virtual environment installed dependencies using the README command.
Only the committed example configuration was used for startup checks.

| Check | Result |
| --- | --- |
| `python -m pip install -e '.[test]'` | Passed in a fresh environment |
| `python -m pip check` | No broken requirements |
| `python -m pytest -q` | 318 passed |
| Browser suite in `tests/web/studio.spec.cjs` | All checks passed |
| Home page and interactive API documentation | HTTP 200 |
| Native font availability | 26 of 26 supported target languages |
| README and implementation-guide local links | No missing targets |
| Staged files checked against configured secret values | No matches |
| Git whitespace check | Passed |

The unrestricted dependency range initially installed PyMuPDF 2.0 and produced
five table-detection/alignment regression failures. The release now requires
`pymupdf>=1.28,<2`. Verification was repeated in a second fresh virtual environment
with PyMuPDF 1.28.2; all 318 tests passed. Other resolved dependencies included
FastAPI 0.162.0, SQLAlchemy 2.1.6 and pytest 9.2.0.

The tests use mocked external providers and synthetic documents. No live
translation, OCR or AI repair calls were made for this release verification.
Passing tests do not establish translation accuracy on arbitrary user documents
or production hosting readiness. Live demonstration cases still need visual and
linguistic inspection.

Browser coverage includes responsive layouts, paired review highlights,
triaging either decision out of pending findings, the changes queue, selected AI
repair, invalid/rejected requests, connection/reload recovery and final/previous
PDF download controls. See the [browser guide](../tests/web/README.md).

## Deterministic layout policy follow-up

The follow-up layout change passes **338 Python tests**, including preservation of
saved full/partial translations, source-paragraph reconstruction across extraction
blocks, resume/form/list/column boundaries, bounded joint fitting and Tamil shaped
text measurement. No external model calls were used in this verification.

A source-only audit of 100 single-page benchmark PDFs found no lost or duplicated
source spans. A paired render comparison against commit `79f062e` covered the
first six pages of the pinned DocLayNet suite (one per document category), three
layout modes and identity/expanded profiles: **36 cases per implementation**.

- All 12 preserve-layout cases had identical layout records, retention, issue
  counts and rendered pixels at the default rendering resolution.
- No paired case gained a hard rendering issue or retained more source spans.
- Across the 36 cases, retained-source span instances decreased by 100. These
  are per-case counts across repeated mode/profile inputs, not 100 distinct pages.
- Tiny fitted-block counts decreased by 14; grouping changes the number of blocks,
  so this is a diagnostic count rather than a comparable visual-quality rate.

Reports and visual outputs are saved under the ignored directories
`output/benchmarks/layout-policy-baseline-20261029/`,
`output/benchmarks/layout-policy-final-20261029/`, and
`output/layout-policy-review-final/`. The latter includes a layout-only Tamil
replay using already saved translations. No production database or original
output was modified. Both replay pages were visually inspected; this checks
layout, not linguistic translation accuracy.

The six-page render sample is a regression diagnostic. It does not establish
correctness for every PDF or replace visual review of dense/ambiguous documents.
Reproduce the source audit with `python -m scripts.audit_layout_policy --sources
PATH_TO_PDFS --output NEW_REPORT.json`, and see [the policy guide](layout-policy.md)
for the optimization rules and limits.
