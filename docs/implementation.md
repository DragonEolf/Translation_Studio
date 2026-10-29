# Translation Studio implementation notes

For installation, usage, and the project overview, see the [README](../README.md).

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

After translation, choose **See issues · compare PDFs** to open a document review
workspace. Original and output page previews appear side by side with matching
region highlights, page navigation, zoom, optional synchronized scrolling, and
an automatic-findings summary bar. The legend distinguishes **Needs attention**
(red), **Check visually** (amber), **No automatic issue** (blue), and **You marked
okay** (green). Click a highlight or list entry to compare source/candidate text,
read the findings, and see suggested inspection steps. Page/document findings
without precise coordinates are listed without inventing a highlight. Missing
previews show an explicit fallback message; the PDF download remains available.

Mark regions **Looks okay** or **Needs changes** after inspecting them. These
decisions are stored in session storage for this browser tab and output version;
document text is not stored there. A changed output invalidates previous marks.
Either mark removes the region from pending findings and their highlights;
**Needs changes** moves it into a separate changes queue. Run **Repair marked
regions with AI** below the review after completing your selections.
Reviewed regions remain available through **Marked okay**, **Needs changes queue**,
and **All regions**. Clearing a decision returns it to pending. Marks do not edit
the PDF or erase the original diagnostics. The summary bar counts unmarked text
regions, while pending findings and review decisions are reported separately. No automatic issue is a rendering-check
result, not a guarantee of semantic translation accuracy. Review uses existing
previews and the read-only `GET /v1/documents/ID/review` endpoint, with no provider
calls; active jobs must finish before a review snapshot can be loaded.

Truncated individual passages are retried as smaller sentences, clauses, or
word-separated pieces. Recovery preserves source order, line separators, protected
numbers/identifiers, and rich-run style ownership. It stops after five split
levels or 31 provider calls per passage; authentication and service errors do not
trigger splitting. Recovered passages carry a sentence-flow review warning.
If any piece still fails, the whole region retains its source text. Translation
metadata records request sizes, finish reasons, truncation flags, usage (including
discarded responses), and split counts. Failed-passage diagnostics and batch
request diagnostics are saved in the job's `translation_batch_stats` payload;
these diagnostic records do not include request or response text.

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

Poll each returned job until terminal status before starting the next operation on the same document. Operations on different documents can run concurrently. Jobs survive restarts; interrupted jobs return to the queue. Caching is disabled by default (`STUDIO_CACHE_ENABLED=false`): each translation request runs fresh, inspection calls OCR again when needed, and rendering rebuilds every page. Translation revisions remain stored in SQLite for history, rendering and manual edits; storing those records does not skip fresh translation requests. Cache reuse can be explicitly enabled with `STUDIO_CACHE_ENABLED=true`.

Configure `STUDIO_FONT_DIR` with Noto TTF/OTF fonts for your target scripts. Font files are validated when the application starts. Native Indic output requires the corresponding Noto script font; missing fonts produce `unsupported_glyphs` review issues and retain original text. English/roman output uses MuPDF's built-in fonts. `/v1/health` reports available fonts. Noto fonts can be obtained from https://fonts.google.com/noto.

If a selected font lacks a character, only that character uses a verified
installed fallback, preferring Noto Sans with the matching weight. Supported
text retains its original selected font; no fallback spans are added unless
needed. Fitting, table-cell measurement, local checks and rendering use
the same fallback rules. This lets Arabic-script text retain symbols such as
`₹` without reverting the whole translated line to English. Characters unsupported
by all available fonts still produce a review issue and retain source text.

To install Regular/Bold fonts for all currently supported target scripts, run `python -m scripts.download_fonts` from the project root and set `STUDIO_FONT_DIR=./fonts`. The downloader skips existing files, validates downloaded fonts, and saves their sources and sizes in `fonts/supported/manifest.json`. Restart the server after adding fonts.

Default limits: 25 MB, 100 pages. Encrypted documents are rejected. Digital pages use direct extraction; image-only pages automatically use Bodhan OCR. Blank pages are preserved. Up to four document jobs run concurrently by default; PDF operations are serialized behind a process-wide lock. Bodhan calls retry transient failures with a bounded budget, and provider secrets never appear in returned errors.

## Scanned PDFs and OCR

OCR adapters may optionally return `polygon_xy`: an ordered, non-self-intersecting list of 3-256 `[x, y]` vertices in input-image pixels, contained within `bbox_xyxy`. These coordinates are validated and stored as `style.source_polygon` in PDF points. The renderer clips the replacement background to that polygon; fitting and translation placement still use the rectangular box. Bodhan currently supplies rectangles, so existing requests retain rectangular regions. This adds polygon support, not automatic curved-text detection or recognition rectification.

Use the same upload and translation endpoints for scanned PDFs. Inspection sends one PNG per scanned page to Bodhan's `indic-ocr` model and converts its pixel coordinates to PDF points. Mixed documents use OCR only for image-only pages; pages with an existing text layer continue through direct extraction. OCR response files are read/written only when caching is explicitly enabled.

If your translation key does not authorize OCR, add `BODHAN_OCR_API_KEY=your_ocr_key` to `.env`. Otherwise OCR falls back to `BODHAN_API_KEY`. The OCR key should have access to `indic-ocr`. `STUDIO_OCR_DPI=150` controls image resolution. No live OCR call is performed by tests; tests use mocked providers.

Scans have no reliable font metadata, so font sizes and styles are estimated. The renderer replaces recognized text regions with locally estimated paper backgrounds and inserts searchable translated text while preserving image pixels outside those regions. Background estimation samples a nearby band, excludes other text boxes and dark ink, and fits a gentle colour gradient to follow lighting. This approximates paper colour; it does not reconstruct notebook ruling or paper texture. Scanned tables and equations remain in the original image with review issues. Colored backgrounds, handwriting, complex layouts, and low-quality scans need visual review. OCR pages receive an `ocr_layout_estimated` warning; detection confidence is not a guarantee of transcription accuracy. Uploading a scan invokes a billable OCR operation under your Bodhan account.

To compare saved OCR output without making new API calls or modifying the database, run `.venv/bin/python -m scripts.compare_scan_backgrounds DOCUMENT_ID`. This writes the OCR pages and a three-column original/white/local-background comparison under `output/pdf/scan-background-review/`, plus a verification report. It reuses the latest saved translations and layouts, so existing recognition or translation errors remain visible. Add `--refit-ocr --output-dir output/pdf/ocr-fitting-review` to compare saved fitting against freshly measured OCR layouts while preserving the saved translations.

Small math-only OCR detections that substantially overlap a larger passage are held for review with `ocr_overlap_review_required`. Their original text remains stored, but a separate replacement is skipped to avoid printing a misread duplicate on top of the passage. The check also runs when re-rendering saved OCR blocks. Non-overlapping expressions and ordinary currency remain unaffected.

OCR math symbols such as `$\rightarrow$` and `$\Rightarrow$` are displayed as Unicode arrows consistently during fitting, PDF rendering, searchable text generation, and verification. Saved OCR and translation strings remain unchanged. Currency values such as `$100` remain literal. Unsupported or malformed expressions produce an `unsupported_ocr_notation` review warning rather than being silently stripped.

OCR text retains explicit line and paragraph breaks during fitting and rendering. OCR regions whose source text mentions a signature (or whose style marks a signature) are retained with `signature_review_required`, because the region may combine printed labels and handwriting. This conservative rule is not a handwriting detector; it can retain printed signature instructions and miss signatures without those cues.

In preserve-layout (`spans`) mode, selectable text is translated as independent source spans. Paragraph and auto modes conservatively group source spans before translation, retaining the original boxes and formatting references. Geometry upgrades retain matching revisions; changed source groups require fresh translation.

Translation requests batch text spans as escaped XML, keeping page boundaries and local span-to-box mappings. Defaults are 30 spans and 6,000 serialized characters per batch, configurable with `STUDIO_TRANSLATION_BATCH_SIZE` and `STUDIO_TRANSLATION_BATCH_CHARS`. Numeric/code-only spans do not consume translation calls. Saved translations are reused only when caching is explicitly enabled. Single spans continue through the individual translator.

Every batch requires well-formed text-only XML, exactly the expected ordered IDs, no extra attributes or unparsed content, nonempty span text, no truncation, and unchanged protected literals in their own spans. XML entities are decoded exactly once. Invalid batches split recursively; single-span fallback retains the existing protected-literal retry behavior. Authentication and transport/provider failures propagate rather than triggering a flood of smaller requests. Each accepted span commits independently; reuse after a restart requires the explicit cache opt-in. Translation metadata records batch IDs and sizes, and each successful batch's token usage is assigned once. Job payloads expose `translation_batch_stats` (batch calls, rejected batches, individual calls, and tokens spent on batch requests); individual calls may internally retry, so these counts are not raw HTTP attempt counts. Structural validation does not establish semantic translation quality.

The block classifier uses font/position heuristics and detects ruled tables through PyMuPDF. Complex tables, formulas, decorative layouts, and semantic translation adjudication are not guaranteed. Failed fitting retains source text and adds manual-review issues. The checker validates rendered text, protected literals, collisions, and pixels outside replacement regions. Issues remain available for manual review. The revise endpoint returns `reasoning_disabled`. Direct text correction is available, including for failed translation blocks.

## Rendering and repair

Detected table cells label their contained spans without merging their text or changing their rectangles. Font selection prefers normal-width static Regular or Bold Noto files, tries Condensed variants when necessary, and supports punctuation/bullet fallback. Fixed digital spans use a bounded binary search for the largest fitting positive font size, with no minimum font-size policy or readability warning. OCR blocks use the same measured binary search inside their original region, with an 8 pt floor. Newly extracted OCR blocks use 24 pt as a search ceiling rather than inferring a physical font size from OCR newline counts. This also repairs saved OCR blocks whose earlier estimate was too large. Text that still cannot fit is retained in the source language and flagged.

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

## Table-cell alignment checks

The checker flags horizontal or vertical centering mismatches when the original
selectable PDF text provides evidence that the cell was centered. After fitting
and typography, a local pass centers translated lines horizontally and the whole
translated cell group vertically, preserving spacing between paragraphs. It uses
the same shaped text geometry as rendering, including Indic text before outlining.
Cells containing images, missing translations, unsupported output or rotated text
are excluded from repair. Repairs must remain inside the cell and pass the normal
collision, clipping and retained-text checks; rejected changes are rolled back.
Scanned cells and ambiguous source alignment still require visual review. The
layout report records attempted alignment changes; accepted status is reported
separately. Existing PDFs require rerendering to receive these changes.

## Typography consistency and local layout checks

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

AI repair runs only when requested from the review changes queue. Set
`OPENROUTER_API_KEY` and optionally `STUDIO_REPAIR_MODEL` (default
`openai/gpt-4.1`, a vision-capable model). The model receives source/output crops,
source and translated text, local findings, and optional reviewer notes for each
selected region. It proposes text, per-run wording, alignment, font size or local
box changes. Only selected regions can change. Styled runs, protected literals,
font support, minimum font size, cell/page bounds, collisions and rendered content
are validated locally. OCR text regions are supported; complex scanned tables,
rotated text and unlocated page/document findings require manual review.

The bottom of the review contains repair notes, the repair button, per-region
outcomes and the final PDF download. Accepted repairs get new translation revisions
and a review-again warning. Rejected candidates keep their previous content; a
provider failure discards the batch. The preceding output is saved as
`previous.pdf` and exposed through **Download previous PDF**. Old review marks
expire when the output changes; unsuccessful selections remain queued. Repairs
are queued, restartable and cancellable through the normal job API. Browser
reconnection reuses the same request ID to avoid duplicate submissions. No live
provider calls are made in the test suite.

`POST /v1/documents/ID/review/repair` accepts `version` from the review snapshot,
`request_id`, `region_ids`, and optional `instruction`. A stale snapshot is rejected.
The job payload exposes `repair_results` and `accepted`; accepted batch details
are also saved in `repair-report.json`. The default limit is 20 regions per request.
The legacy per-block revise endpoint still returns `410 reasoning_disabled`;
historical repair providers remain isolated for benchmark reproduction.

## Automatic transliteration and shared layout

Select **Transliterate remaining English** before starting, or send
`"transliterate": true` with the translation request. For native Indic targets,
the app translates first, calls Bodhan once per distinct eligible English word,
then applies all replacements to text and styled runs before producing the PDF.
Acronyms use English letter names. URLs, identifiers, numbers and units are
protected. English and roman/codemix targets reject this option. A failed
transliteration request fails the job without applying a partial set of replacements.
The existing selective preview/apply API remains available for later corrections.

Auto mode allocates cell space even for short table paragraphs. Shared cell
layout includes paragraphs that overflowed individually, fits them together,
and respects a body-size floor of `STUDIO_TYPOGRAPHY_MIN_FONT_PT` (6pt by default).
A single overflowing rich paragraph can use its available cell area. Missing
translations, unsupported text, other cell content and images remain barriers.

Nearby body paragraphs may share their combined geometry when one overflows or
shrinks severely. Headings, captions, lists, cells, footnotes, scans, images,
visible rules and other content prevent this flow. Their translations stay
separate. Source spans are grouped before translation, so Bodhan receives the
complete source paragraph; translated blocks are never arbitrarily concatenated.

Each changed page is rendered and checked before and after the local passes.
New per-block checker errors or loss of a previously fitted translation cause
rollback. `layout-report.json` records attempted changes, acceptance, issue
counts, whether issues decreased, and each translation's ID, text hash and
retention outcome. Failure reasons distinguish minimum size, insufficient cell
height, missing glyphs, unavailable fonts and rendering mismatch. These checks
provide evidence of local improvement, not a guarantee of visual correctness.

Strict escaped XML and ordered-ID validation remain enabled. Invalid responses use bounded splitting and local validation rather than being accepted silently.

Database initialization adds unique page/revision indexes and query indexes,
relationships, foreign keys for new databases, and validation triggers for both
existing and new SQLite files. Invalid statuses, progress, page references and
cross-document issue references are rejected. Existing records are preserved;
pre-existing duplicate page/revision keys must be resolved before unique indexes
can be installed. Back up production databases before upgrading.

The interface includes the [official IIT Madras logo](https://www.iitm.ac.in/themes/custom/iitm/assets/images/iitm_logo.png)
and the credit **Made by Krishna Karthikeyan**.

## DocLayNet rendering benchmark

A reproducible 40-page digital-PDF suite compares `spans`, `paragraphs` and `auto`
using fixed original text and synthetic expansion. It runs without translation,
OCR or vision calls, stores isolated databases, and generates a visual review
report with retained-content, fitting and typography diagnostics. See
[the benchmark guide](../benchmarks/doclaynet/README.md) for preparation, commands,
limitations and optional Indic/RTL profiles.

## UGC English–Hindi benchmark

The [UGC benchmark guide](../benchmarks/ugc/README.md) describes a first-50-page
real-document suite. Studio translates pages sequentially while a separate
OpenRouter worker scores similarity between finished Hindi outputs and the
same-numbered official Hindi pages. The judge receives only these two Hindi documents. Execution requires an explicit `--live` flag; `--dry-run` only prints
the plan. Per-page results, annotated PDFs and resumable progress are saved.
