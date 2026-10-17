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

Open http://127.0.0.1:8000/ for the upload/download interface. Choose a PDF and target language, then click Translate PDF. The page waits for inspection and translation, displays progress and retained-source/review counts, and offers the output PDF for download. Failed reconstruction offers a clearly labelled draft instead.

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

Poll each returned job until terminal status before starting the next document operation. Jobs survive restarts; interrupted jobs return to the queue. Caching is disabled by default (`STUDIO_CACHE_ENABLED=false`): each translation request runs fresh, inspection calls OCR again when needed, and rendering rebuilds every page. Translation revisions remain stored in SQLite for history, rendering and manual edits; storing those records does not skip fresh translation requests. Cache reuse can be explicitly enabled with `STUDIO_CACHE_ENABLED=true`.

Configure `STUDIO_FONT_DIR` with Noto TTF/OTF fonts for your target scripts. Font files are validated when the application starts. Native Indic output requires the corresponding Noto script font; missing fonts produce `unsupported_glyphs` review issues and retain original text. English/roman output uses MuPDF's built-in fonts. `/v1/health` reports available fonts. Noto fonts can be obtained from https://fonts.google.com/noto.

To install Regular/Bold fonts for all currently supported target scripts, run `python -m scripts.download_fonts` from the project root and set `STUDIO_FONT_DIR=./fonts`. The downloader skips existing files, validates downloaded fonts, and saves their sources and sizes in `fonts/supported/manifest.json`. Restart the server after adding fonts.

Default limits: 25 MB, 100 pages. Encrypted documents are rejected. Digital pages use direct extraction; image-only pages automatically use Bodhan OCR. Blank pages are preserved. Background work runs serially; document processing uses threads for PDF rendering. Bodhan calls retry transient failures with a bounded budget, and provider secrets never appear in returned errors.

## Scanned PDFs and OCR

Use the same upload and translation endpoints for scanned PDFs. Inspection sends one PNG per scanned page to Bodhan's `indic-ocr` model and converts its pixel coordinates to PDF points. Mixed documents use OCR only for image-only pages; pages with an existing text layer continue through direct extraction. OCR response files are read/written only when caching is explicitly enabled.

If your translation key does not authorize OCR, add `BODHAN_OCR_API_KEY=your_ocr_key` to `.env`. Otherwise OCR falls back to `BODHAN_API_KEY`. The OCR key should have access to `indic-ocr`. `STUDIO_OCR_DPI=150` controls image resolution. No live OCR call is performed by tests; tests use mocked providers.

Scans have no reliable font metadata, so font sizes and styles are estimated. The renderer covers recognized text boxes with white backgrounds and inserts searchable translated text while preserving image pixels outside those boxes. Scanned tables and equations remain in the original image with review issues. Colored backgrounds, handwriting, complex layouts, and low-quality scans need visual review. OCR pages receive an `ocr_layout_estimated` warning; detection confidence is not a guarantee of transcription accuracy. Uploading a scan invokes a billable OCR operation under your Bodhan account.

OCR text retains explicit line and paragraph breaks during fitting and rendering. OCR regions whose source text mentions a signature (or whose style marks a signature) are retained with `signature_review_required`, because the region may combine printed labels and handwriting. This conservative rule is not a handwriting detector; it can retain printed signature instructions and miss signatures without those cues.

Selectable text uses one independent block per nonempty PyMuPDF text span, including spans inside detected table cells. Each span keeps its own exact rectangle, style, translation and render result; spans are never concatenated into a row or paragraph. These rectangles remain fixed during fitting. Wrapped source lines and style changes therefore translate independently, which can reduce sentence context. An upgrade preserves old revisions only when source text and rectangle exactly match a new span. Combined paragraph/row translations remain in history but must be translated again for the new independent spans.

Translation requests batch text spans as escaped XML, keeping page boundaries and local span-to-box mappings. Defaults are 30 spans and 6,000 serialized characters per batch, configurable with `STUDIO_TRANSLATION_BATCH_SIZE` and `STUDIO_TRANSLATION_BATCH_CHARS`. Numeric/code-only spans do not consume translation calls. Saved translations are reused only when caching is explicitly enabled. Single spans continue through the individual translator.

Every batch requires well-formed text-only XML, exactly the expected ordered IDs, no extra attributes or unparsed content, nonempty span text, no truncation, and unchanged protected literals in their own spans. XML entities are decoded exactly once. Invalid batches split recursively; single-span fallback retains the existing protected-literal retry behavior. Authentication and transport/provider failures propagate rather than triggering a flood of smaller requests. Each accepted span commits independently; reuse after a restart requires the explicit cache opt-in. Translation metadata records batch IDs and sizes, and each successful batch's token usage is assigned once. Job payloads expose `translation_batch_stats` (batch calls, rejected batches, individual calls, and tokens spent on batch requests); individual calls may internally retry, so these counts are not raw HTTP attempt counts. Structural validation does not establish semantic translation quality.

The block classifier uses font/position heuristics and detects ruled tables through PyMuPDF. Complex tables, formulas, decorative layouts, and semantic translation adjudication are not guaranteed. Failed fitting retains source text and adds manual-review issues. The checker validates rendered text, protected literals, collisions, and pixels outside replacement regions. Issues are passed to the disabled reasoning provider for manual review; a future adapter must also validate and apply any proposed correction. The revise endpoint returns `reasoning_provider_not_configured`. Direct text correction is available, including for failed translation blocks.

## Rendering and repair

Detected table cells label their contained spans without merging their text or changing their rectangles. Font selection prefers normal-width static Regular or Bold Noto files, tries Condensed variants when necessary, and supports punctuation/bullet fallback. Fixed digital spans use a bounded binary search for the largest fitting positive font size, with no minimum font-size policy or readability warning. OCR blocks retain the previous 8 pt policy. Text that still cannot fit is retained in the source language and flagged.

Indic glyphs are shaped and stored as vector outlines, accompanied by a block-level searchable Unicode `ActualText` layer. This avoids broken or duplicated Unicode from complex ligatures; search/selection operates at block granularity. Plain Latin text remains native PDF text. Graphics and image regions outside edited boxes are checked against the original.

Numbers, codes and URLs that need no translation are copied directly. If a model changes placeholders, the individual translator retries natural text with literal validation, then translates prose around verbatim literals. When caching is enabled, saved translations are reused only when their source text still matches. Original translation revisions are retained.

Document responses include `translation_summary`; finished jobs include `payload.result`, showing rendered versus retained source blocks. If no blocks can render, the job fails with `reconstruction_failed` and writes `draft.pdf`, rather than claiming a successful final translation. Partial exports still report issues and retained source counts.

To repair a previously processed document with the current code without running another worker:

```bash
python -m app.cli DOCUMENT_ID --target hi
# Rerender saved translations only, without model calls:
python -m app.cli DOCUMENT_ID --render-only
```

The repair command preserves a PDF backup and a SQLite snapshot. Do not run it while the same document has an active job. Restart the API server after code changes so new requests use the updated pipeline.

Run verification with `pytest`. Provider tests use HTTPX mocks; live translation requires your key and is billed by Bodhan.
