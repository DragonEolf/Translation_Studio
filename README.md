# Translation Studio API

Local PDF translation with Bodhan, persisted jobs, block revisions, and in-place rendering.

Requires Python 3.12 or later and PyMuPDF 1.28 or later. Run one application process so only one worker consumes the SQLite queue.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Set BODHAN_API_KEY in .env
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Interactive API documentation: http://127.0.0.1:8000/docs.

Open http://127.0.0.1:8000/ for the single-page upload/download interface. Language
options show English and native names. **Automatic** is the interface default:
within each conservatively detected paragraph, 1–3 spans retain their individual
boxes and groups of 4 or more spans use paragraph reflow. This is a starting
heuristic for text fragmentation, not a learned prediction of output quality.
Choose **Preserve original layout** to keep
individual text boxes fixed (`spans`), or **Improve readability** for experimental
paragraph reflow with inline formatting (`paragraphs`).
Both modes can shrink text on dense pages; the readability option is not a
minimum-font-size guarantee. OCR pages use their existing reconstruction path.

Standalone single-span lines also infer centring from source geometry, so short
centred headings and table labels retain centring in Automatic mode. Leading and
trailing spaces used as manual positioning are excluded from the visible text
box; their full source region is still used for redaction. Underline styles
require a verified owned decoration path: the extractor's underline flag alone
can mistake nearby table borders for text decoration.

Native Urdu (`ur`), Arabic-script Kashmiri (`ks`), and Arabic-script Sindhi
(`sd-Arab`) use RTL paragraph direction and right-aligned ordinary text.
Centred headings and justified text retain their alignment. The same rule applies
to fitting, table-cell measurement and final rendering, including rich text.
Roman-script output remains LTR. Page elements and images retain their positions;
this basic support does not mirror the page or rearrange columns.

The page validates file type, empty files and upload size before sending, and
explains server-side page limits, encrypted/invalid PDFs, OCR access and provider
errors. Separate stages show upload, reading, translation and PDF preparation.
Partial results show retained-source counts and plain-language review reasons.
No-text documents are labelled as preserved originals, and failed reconstruction
offers a clearly labelled source-retaining draft rather than a successful result.

After a native-script translation, **Transliterate remaining English** lists
eligible words from successfully rendered translations. Select up to 20 unique
words, preview their native-script pronunciations, uncheck any unwanted result,
and apply the selection to render an updated PDF. All occurrences of a selected
word are replaced in eligible text. Uppercase abbreviations use English letter
names (`VGF` sends `vee gee eff`); other words are sent as written. Pronunciations
are model suggestions and may be approximate. This does not expand acronyms or
translate their meaning. Preview generation alone does not change the PDF.

This uses `POST https://api.bodhan.ai/transliterate` with the existing
`BODHAN_API_KEY`; no additional key setting is needed. Only selected words (or
letter names), not whole pages, are sent. URLs, emails, protected identifiers,
numbers, common units and words split across style runs are excluded. English
and Roman-script output do not offer this action. Text that remained in the
source language because rendering failed is excluded as well.

The API exposes `GET /v1/documents/ID/transliteration-candidates`,
`POST /v1/documents/ID/transliteration-preview` (`words`, optional `request_id`),
and `POST /v1/documents/ID/transliteration-apply` (`preview_job_id`, `words`).
Preview is a queued, cancellable job; its suggestions are in the job payload.
Applying preserves rich-run styles, creates translation revisions, and queues
local rendering. Stale previews are rejected. Reusing a preview request ID or
retrying an identical apply returns the existing job without duplicate work.

The **Cancel translation** button cancels queued work or requests cancellation of
active reading/translation/rendering. Pending API waits are interrupted; PDF work
stops at safe checkpoints, so the button may briefly show “Cancelling…”.
`POST /v1/jobs/JOB_ID/cancel` is idempotent: active jobs become `cancelling`, then
`cancelled`; completed jobs keep their result. Cancelling jobs continue to lock
the document until the worker stops. Already submitted external requests may
still be processed or billed by the provider. Cancellation requests survive a
server restart and are not resumed as new work.

The current tab stores active job IDs and chosen preferences in session storage
(no document text). After a connection interruption or page reload, it checks the
same job. The document response includes `latest_job` metadata to recover a lost
translation-submission response without starting another translation. Health
metadata reports configured services and available target fonts without exposing
credentials. Browser checks and setup instructions are in `tests/web/README.md`.

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

If a selected font lacks a character, only that character uses a verified
installed fallback, preferring Noto Sans with the matching weight. Supported
text retains its original selected font; no fallback spans are added unless
needed. Fitting, table-cell measurement, vision repair checks and rendering use
the same fallback rules. This lets Arabic-script text retain symbols such as
`₹` without reverting the whole translated line to English. Characters unsupported
by all available fonts still produce a review issue and retain source text.

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

### Paragraph layout with inline formatting

The upload form offers **Automatic** (default), **Improve readability** (paragraph
mode, experimental), and **Preserve original layout** (original text boxes).
Paragraph mode translates a complete text group,
then lays it out with one shared base size while applying the original bold,
italic, underline, colour, superscript markers, and relative font sizes to individual translated runs. Every
source span and its original box is retained in `structure.json`.

For API callers, opt in with `"layout_mode": "auto"` or `"paragraphs"` on the translation
request; the API default remains `"spans"`. The selected mode persists for later
render requests. Switching modes retains translation history and regenerates
active geometry from the original PDF. Caching remains disabled by default.

Groups are conservative: they stay within source PDF text blocks, avoid crossing
columns and table cells, and separate bullet items. Table groups can use free space inside their cell,
bounded by cell borders and neighboring text. This is a layout heuristic,
not a guarantee of semantic paragraph detection. Scanned PDFs keep the existing
OCR path.

Translation uses sparse XML inline codes mapped to local style definitions.
Validation checks paragraph/code membership, protected values inside formatted
runs, and bounded code splitting/reordering. Invalid batches are split; if an
individual paragraph still fails, its formatted runs are translated separately
and a review warning is recorded. This fallback can weaken sentence flow.

Paragraph translations are also exported as XLIFF 2.2 with original formatting
references at `/v1/documents/{id}/artifacts/translation.xlf`. The original page
geometry stays in `structure.json`; XLIFF carries source/target text and inline
formatting codes. Plain-text API edits of mixed-format paragraphs are rejected
rather than silently discarding their formatting; use span mode for those edits.

Related rich-text paragraphs in the same table cell now share one typography
scale. Paragraphs reflow vertically through the cell's available area, with
consistent spacing, rather than competing for their original line boxes. Equal
original font sizes produce equal translated sizes; different original sizes
retain their ratios, including inline footnotes and superscripts. This applies
within each cell, not globally across unrelated cells. Missing translations,
unsupported text, side-by-side columns, and images interrupt shared flow.

Underlines detected in selectable PDF text are stored as a local text style,
alongside bold and italic. They follow translated runs as text wraps or changes
size in both layout modes. Recognized thin horizontal underline paths are removed
from their original positions only when all their owning source spans render;
untranslated source text keeps its original decoration. Table borders and unrelated
rules remain intact. This handles simple drawn underlines, not arbitrary decorative
paths, underline annotations, or underlines embedded in scanned images. Existing
paragraph translations may need translation again because underline detection can
split source runs and changes their formatting hash.

## Typography consistency and optional vision repairs

After individual fitting and shared table-cell layout, a local pass compares
nearby blocks of the same role in the same column/cell. It penalizes differences
in **relative shrinkage** (`output_size / original_size`), preserving intentional
heading/body and inline size ratios. Superscripts, OCR blocks and vertical text
are excluded from smoothing. It tries at most 24 exact layout measurements per
page, changes only font size, and never moves boxes or changes translated text.
An unfixable tiny block does not force its readable neighbours down to its size.
Disable this pass with `STUDIO_TYPOGRAPHY_ENABLED=false`.

`STUDIO_TYPOGRAPHY_MIN_FONT_PT=6` is the default target for repair and warning
generation, not a hard rejection of the original fitted result. Existing small
source text and deliberately small inline superscripts retain their hierarchy.
Regions that cannot be improved safely remain rendered with `small_text` or
`font_size_inconsistent` review warnings; there is no guarantee that all output
will exceed 6 pt. The target and refit budget are configurable in `.env.example`.

The DeepSeek vision reviewer is **off by default**, even if an API key is present.
To opt in, set `STUDIO_VISION_REVIEW_ENABLED=true`, select
`STUDIO_VISION_PROVIDER=openrouter`, and configure `OPENROUTER_API_KEY`. The default
OpenRouter model is `deepseek/deepseek-v4.1-flash-1017`. Alternatively select
`STUDIO_VISION_PROVIDER=deepseek`, configure `DEEPSEEK_API_KEY`, and use the default
direct model `deepseek-flash`. `STUDIO_VISION_MODEL` overrides the model; select
one that accepts images. Restart the server after configuration changes.

**Enabling review sends source and rendered crops of flagged document regions to
that provider**, with additional image/token charges. The interface discloses
when review is enabled. No screenshots are sent when disabled. Default limits
are three region attempts per document, one proposal per region, and a 20-second
request timeout. Provider failures stop further attempts for that document and
retain the locally generated PDF.
Thinking mode is disabled for this small structured review to bound latency and
output tokens. This stage is separate from the still-disabled instruction-based
translation revision endpoint.

The reviewer can propose only `set_font_size`, `set_bbox`, `set_font_width`
(`normal`, `semi_condensed`, `condensed`), `no_change`, or `manual_review`.
No text edits, code, image deletion, arbitrary font paths, or decoration removal
are allowed. Boxes stay inside the page/cell, move at most 24 pt per coordinate,
and grow by at most 50% in area without increasing overlap with other content.
Original-box mode rejects box changes. Proposed sizes respect original relative
run sizes and the configured readable-size target. Font widths must be installed
and support the required glyphs.

The candidate is measured, rendered separately and checked against the original.
It is accepted only if local quality improves and no hard check category gets
worse. Otherwise the original render is kept. This is a bounded heuristic, not
proof of visual correctness: unflagged artifacts such as raster text shadows may
still be missed. Review counts, accepted/rejected suggestions and typography
diagnostics are stored in job `payload.layout_review_stats` and the downloadable
`/v1/documents/{id}/artifacts/layout-report.json` artifact.

All automated vision tests use mocked HTTP responses or injected reviewers; they
do not send test or user documents to an external vision service.
