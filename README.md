# Translation Studio API

Local PDF translation with Bodhan, persisted jobs, block revisions, and in-place rendering.

Requires Python 3.12 or later. Run one application process so only one worker consumes the SQLite queue.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Set BODHAN_API_KEY in .env
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Interactive API documentation: http://127.0.0.1:8000/docs.

```bash
curl -F 'file=@input.pdf' http://127.0.0.1:8000/v1/documents
curl http://127.0.0.1:8000/v1/jobs/JOB_ID
curl -X POST http://127.0.0.1:8000/v1/documents/DOCUMENT_ID/translations \
  -H 'Content-Type: application/json' -d '{"target_language":"hi","script":"native"}'
curl http://127.0.0.1:8000/v1/documents/DOCUMENT_ID/blocks
curl http://127.0.0.1:8000/v1/documents/DOCUMENT_ID/issues
curl -X PUT http://127.0.0.1:8000/v1/blocks/BLOCK_ID/translation \
  -H 'Content-Type: application/json' -d '{"text":"Corrected translation"}'
curl -o translated.pdf http://127.0.0.1:8000/v1/documents/DOCUMENT_ID/artifacts/final.pdf
```

Poll each returned job until terminal status before starting the next document operation. Jobs survive restarts; interrupted jobs return to the queue. Repeating a failed translation request reuses successful block translations for that language/script. Revisions remain stored in SQLite. PDF edits rebuild the affected page from the original, and export assembles cached unaffected pages.

Configure `STUDIO_FONT_DIR` with Noto TTF/OTF fonts for your target scripts. Font files are validated when the application starts. Native Indic output requires the corresponding Noto script font; missing fonts produce `unsupported_glyphs` review issues and retain original text. English/roman output uses MuPDF's built-in fonts. `/v1/health` reports available fonts. Noto fonts can be obtained from https://fonts.google.com/noto.

Default limits: 25 MB, 100 pages. Encrypted documents are rejected. Digital pages use direct extraction; image-only pages automatically use Bodhan OCR. Blank pages are preserved. Background work runs serially; document processing uses threads for PDF rendering. Bodhan calls retry transient failures with a bounded budget, and provider secrets never appear in returned errors.

## Scanned PDFs and OCR

Use the same upload and translation endpoints for scanned PDFs. Inspection sends one PNG per scanned page to Bodhan's `indic-ocr` model and converts its pixel coordinates to PDF points. Mixed documents use OCR only for image-only pages; pages with an existing text layer continue through direct extraction. Successful OCR responses are cached locally for restarted jobs.

If your translation key does not authorize OCR, add `BODHAN_OCR_API_KEY=your_ocr_key` to `.env`. Otherwise OCR falls back to `BODHAN_API_KEY`. The OCR key should have access to `indic-ocr`. `STUDIO_OCR_DPI=150` controls image resolution. No live OCR call is performed by tests; tests use mocked providers.

Scans have no reliable font metadata, so font sizes and styles are estimated. The renderer covers recognized text boxes with white backgrounds and inserts searchable translated text while preserving image pixels outside those boxes. Scanned tables and equations remain in the original image with review issues. Colored backgrounds, handwriting, complex layouts, and low-quality scans need visual review. OCR pages receive an `ocr_layout_estimated` warning; detection confidence is not a guarantee of transcription accuracy. Uploading a scan invokes a billable OCR operation under your Bodhan account.

The block classifier uses font/position heuristics and detects ruled tables through PyMuPDF. Complex tables, formulas, decorative layouts, and semantic translation adjudication are not guaranteed. Failed fitting retains source text and adds manual-review issues. The checker validates rendered text, protected literals, collisions, and pixels outside replacement regions. Issues are passed to the disabled reasoning provider for manual review; a future adapter must also validate and apply any proposed correction. The revise endpoint returns `reasoning_provider_not_configured`. Direct text correction is available, including for failed translation blocks.

## Rendering and repair

Detected merged table cells are rendered individually; a merged cell no longer makes the whole table unsupported. Cell style comes from text actually inside that cell. Non-table extraction excludes text already captured by table cells. Font selection prefers normal-width static Regular or Bold Noto files, tries Condensed variants when necessary, and supports punctuation/bullet fallback. Dense table cells can shrink to 70% of source size with a 6 pt floor; other blocks retain the previous 8 pt policy.

Indic glyphs are shaped and stored as vector outlines, accompanied by a block-level searchable Unicode `ActualText` layer. This avoids broken or duplicated Unicode from complex ligatures; search/selection operates at block granularity. Plain Latin text remains native PDF text. Graphics and image regions outside edited boxes are checked against the original.

Numbers, codes and URLs that need no translation are copied directly. If a model changes placeholders, the translator retries natural text with literal validation, then translates prose around verbatim literals. Cached translations are reused only when their source text still matches. Original translation revisions are retained.

Document responses include `translation_summary`; finished jobs include `payload.result`, showing rendered versus retained source blocks. If no blocks can render, the job fails with `reconstruction_failed` and writes `draft.pdf`, rather than claiming a successful final translation. Partial exports still report issues and retained source counts.

To repair a previously processed document with the current code without running another worker:

```bash
python -m app.cli DOCUMENT_ID --target hi
# Rerender saved translations only, without model calls:
python -m app.cli DOCUMENT_ID --render-only
```

The repair command preserves a PDF backup and a SQLite snapshot. Do not run it while the same document has an active job. Restart the API server after code changes so new requests use the updated pipeline.

Run verification with `pytest`. Provider tests use HTTPX mocks; live translation requires your key and is billed by Bodhan.
