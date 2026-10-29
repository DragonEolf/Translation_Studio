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
