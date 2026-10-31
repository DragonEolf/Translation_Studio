# One-line file notes

Start with the application files for the walkthrough. Supporting files follow. This inventory covers maintained files; local secrets, environments, caches, and generated run outputs are excluded.

## Setup

- `.env.example` — Lists environment options and placeholders for provider credentials.
- `.gitignore` — Excludes secrets, virtual environments, caches, and generated data from Git.
- `CHECKPOINTS.md` — Records development milestones and implementation checkpoints.
- `README.md` — Explains setup, usage, screenshots, features, and limitations.
- `pyproject.toml` — Declares Python requirements, dependencies, packaging, and test/lint settings.

## Application entry points

- `app/__init__.py` — Package initializer; no processing stage.
- `app/cli.py` — Run or resume an existing document without launching a second queue worker.
- `app/config.py` — Environment-backed application settings and structured service errors.
- `app/main.py` — FastAPI application factory, request validation, and job submission endpoints.
- `app/orchestrator.py` — Coordinate extraction, validated translation, rendering, and recovery.

## Processing agents

- `app/agents/__init__.py` — Package initializer; no processing stage.
- `app/agents/block_maker.py` — Classify extracted spans while retaining stable IDs, styles, and source geometry.
- `app/agents/checker.py` — Check rendered text, protected values, geometry, and changes outside replaced regions.
- `app/agents/inspector.py` — Validate PDF inputs and choose digital, scanned, or blank-page extraction.
- `app/agents/ocr_reader.py` — Validate OCR responses and map pixel coordinates to PDF document regions.
- `app/agents/reorganiser.py` — Choose supported fonts and fit translations while retaining unsafe source regions.
- `app/agents/text_reader.py` — Extract selectable text, table cells, decorations, and layout barriers.
- `app/agents/translator.py` — Translate validated XML batches with protected literals and bounded passage recovery.

## External providers

- `app/providers/__init__.py` — Package initializer; no processing stage.
- `app/providers/bodhan.py` — Bodhan translation client, supported targets, and bounded transport retries.
- `app/providers/ocr.py` — Bodhan OCR client and strict validation of text and page-image coordinates.
- `app/providers/repair.py` — User-requested multimodal repair proposals, never arbitrary model actions.
- `app/providers/transliteration.py` — Calls Bodhan transliteration and the optional OpenRouter pronunciation fallback.

## Database

- `app/models/__init__.py` — Persistent document state and additive SQLite integrity migrations.

## Local services

- `app/services/__init__.py` — Package initializer; no processing stage.
- `app/services/ai_repair.py` — Selected-region repair: stage candidates, check them, then publish with backup.
- `app/services/cancellation.py` — Cooperative cancellation across HTTP awaits and PDF worker threads.
- `app/services/cell_layout.py` — Shared typography and vertical flow for related paragraphs in table cells.
- `app/services/column_flow.py` — Bounded dynamic programming for compatible paragraph layout candidates.
- `app/services/document_review.py` — Read-only review snapshots: PDF coordinates match the rotated page previews.
- `app/services/job_runner.py` — Single-process asynchronous job queue with restart recovery and cancellation.
- `app/services/layout_policy.py` — Deterministic source-layout policy: confident continuation, otherwise anchoring.
- `app/services/layout_tools.py` — Shared exact layout measurement for fitting, repairs and final rendering.
- `app/services/ocr_notation.py` — Convert supported OCR math notation without altering currency or source data.
- `app/services/ocr_overlaps.py` — Identify ambiguous small math detections overlapping a larger OCR passage.
- `app/services/paragraph_flow.py` — Share geometry between nearby body paragraphs without combining translations.
- `app/services/renderer.py` — Render validated text replacements and assemble pages into an output PDF.
- `app/services/rich_text.py` — Paragraph geometry, local styles, and XLIFF 2.2 interchange.
- `app/services/scan_background.py` — Estimate replacement paper from the unmodified scan, never from prior fills.
- `app/services/storage.py` — Local per-document paths and UTF-8 JSON artifact persistence.
- `app/services/table_alignment.py` — Preserve source-centred table cells using measured, shaped text geometry.
- `app/services/text_fitter.py` — Resolve script fonts and measure shaped plain or styled text against PDF bounds.
- `app/services/timing.py` — Content-free job timing with UTC timestamps and monotonic durations.
- `app/services/transliteration.py` — Conservative opt-in replacements in existing translated text and rich runs.
- `app/services/typography.py` — Bounded local smoothing of relative font scales, never global minimum sizing.
- `app/services/underlines.py` — Associate simple PDF underline paths with their owning text spans.

## Browser interface

- `app/web/app.js` — Health check, upload/translation jobs, polling/reconnect/cancellation, downloads and selective transliteration.
- `app/web/iitm-logo.png` — IIT Madras branding displayed by the interface.
- `app/web/index.html` — Browser page structure, upload/options form, progress, result and review controls.
- `app/web/review.css` — Comparison workspace styling and responsive behavior.
- `app/web/review.js` — Comparison workspace, region selection, versioned review marks, repair submission/reconnect and outcomes.
- `app/web/style.css` — Main interface appearance and responsive layout.

## Manual utilities

- `scripts/README.md` — Indexes manual utilities and identifies commands using external services.
- `scripts/audit_layout_policy.py` — Audit source ownership/grouping over PDFs without network calls or rendering.
- `scripts/check_all_languages.py` — Live synthetic smoke test of all native-script targets; no user PDFs.
- `scripts/compare_scan_backgrounds.py` — Re-render saved OCR translations without API calls or database changes.
- `scripts/download_fonts.py` — Install Regular/Bold Noto fonts for every supported target script.
- `scripts/inline_layout_experiment.py` — Synthetic-only live test of two-level IDs and inline PDF formatting.
- `scripts/render_html_pair.cjs` — Renders saved HTML document pairs into PDFs using Playwright with document scripts disabled.
- `scripts/render_inline_results.py` — Render the saved synthetic test responses, including explicit rejected cases.
- `scripts/render_sparse_inline_results.py` — Render saved synthetic sparse-tag results without API calls.
- `scripts/retry_resume_paragraph_test.py` — Retry only failed/fallback groups in the explicitly authorized resume test.
- `scripts/test_bodhan_delimiters.py` — Live, synthetic-only delimiter experiment. Never imports document/job data.
- `scripts/test_paragraph_pipeline.py` — Live synthetic-only end-to-end test of the app's paragraph layout mode.
- `scripts/test_resume_cell_layout.py` — Local-only cell typography verification using existing Tamil translations.
- `scripts/test_resume_paragraph_live.py` — Explicitly requested live resume test. Sends the specified PDF text to Bodhan.
- `scripts/test_sparse_inline.py` — Synthetic-only sparse formatting test. No document or database access.

## Tests

- `tests/fixtures/ocr_novelty.json` — Provides a saved OCR response for extraction, fitting, and rendering tests.
- `tests/test_ai_repair.py` — Tests ai repair behavior and regression cases.
- `tests/test_auto_layout.py` — Tests auto layout behavior and regression cases.
- `tests/test_batching.py` — Tests batching behavior and regression cases.
- `tests/test_cancellation.py` — Tests cancellation behavior and regression cases.
- `tests/test_cell_layout.py` — Tests cell layout behavior and regression cases.
- `tests/test_column_flow.py` — Tests column flow behavior and regression cases.
- `tests/test_doclaynet_benchmark.py` — Harness checks use synthetic PDFs; the downloaded 40-page suite is opt-in.
- `tests/test_doclaynet_boxes.py` — Tests doclaynet boxes behavior and regression cases.
- `tests/test_doclaynet_regressions.py` — Small, offline regressions reduced from the DocLayNet evaluation.
- `tests/test_document_review.py` — Review snapshots must reflect visible geometry without mutating findings.
- `tests/test_downloads.py` — Tests downloads behavior and regression cases.
- `tests/test_font_fallback.py` — Tests font fallback behavior and regression cases.
- `tests/test_format_pilot.py` — Tests format pilot behavior and regression cases.
- `tests/test_inline_layout_experiment.py` — Validation contract for the experimental nested-ID strategy.
- `tests/test_interface_api.py` — Read-only UI metadata must support recovery without exposing credentials.
- `tests/test_layout_policy.py` — Document-family regressions for source reconstruction and safe anchoring.
- `tests/test_manual_utilities.py` — Manual utilities must be safe to import from tests and diagnostic tools.
- `tests/test_ocr.py` — Tests ocr behavior and regression cases.
- `tests/test_ocr_fitting.py` — Tests ocr fitting behavior and regression cases.
- `tests/test_ocr_notation.py` — Tests ocr notation behavior and regression cases.
- `tests/test_ocr_overlaps.py` — Tests ocr overlaps behavior and regression cases.
- `tests/test_openrouter_benchmark.py` — Tests openrouter benchmark behavior and regression cases.
- `tests/test_parallel_jobs.py` — Tests parallel jobs behavior and regression cases.
- `tests/test_priority_workflow.py` — Regressions for automatic transliteration and guarded shared layout.
- `tests/test_reconstruction.py` — Tests reconstruction behavior and regression cases.
- `tests/test_rich_text.py` — Tests rich text behavior and regression cases.
- `tests/test_rtl.py` — RTL is a text layout rule; source boxes and illustrations stay in place.
- `tests/test_scan_background.py` — Tests scan background behavior and regression cases.
- `tests/test_span_alignment.py` — Tests span alignment behavior and regression cases.
- `tests/test_studio.py` — Tests studio behavior and regression cases.
- `tests/test_table_alignment.py` — Source-driven centering, including renderer/checker integration.
- `tests/test_timing.py` — Tests timing behavior and regression cases.
- `tests/test_translation_recovery.py` — Tests translation recovery behavior and regression cases.
- `tests/test_transliteration.py` — Tests transliteration behavior and regression cases.
- `tests/test_typography.py` — Tests typography behavior and regression cases.
- `tests/test_ugc_benchmark.py` — Offline contract tests. No provider calls or user documents are used.
- `tests/test_underlines.py` — Tests underlines behavior and regression cases.
- `tests/test_vision_pipeline.py` — Tests vision pipeline behavior and regression cases.
- `tests/test_vision_review.py` — Tests vision review behavior and regression cases.
- `tests/web/README.md` — Explains how to run the mocked browser verification suite.
- `tests/web/studio.spec.cjs` — Tests the browser UI with mocked APIs, covering uploads, recovery, review, and selected repair.

## Benchmarks

- `benchmarks/README.md` — Documents benchmarks setup and usage.
- `benchmarks/__init__.py` — Marks this folder as a Python package; Offline rendering benchmarks and reproducible dataset preparation.
- `benchmarks/baseline/README.md` — Documents benchmarks/baseline setup and usage.
- `benchmarks/baseline/__init__.py` — Marks this folder as a Python package; Reproducible default-pipeline baseline on local public PDF datasets.
- `benchmarks/baseline/analyze_live.py` — Audit saved real-translation outputs without further model calls.
- `benchmarks/baseline/live.py` — Actual provider translation baseline; preserves PDFs, jobs and partial runs.
- `benchmarks/baseline/run.py` — Save and run 100 digital pages through auto layout with fixed expansion.
- `benchmarks/doclaynet/BOX_FINDINGS.md` — Documents Native PDF bounding-box baseline.
- `benchmarks/doclaynet/FINDINGS.md` — Documents How the first DocLayNet run is being used.
- `benchmarks/doclaynet/README.md` — Documents benchmarks/doclaynet setup and usage.
- `benchmarks/doclaynet/__init__.py` — Marks this folder as a Python package; DocLayNet rendering evaluation.
- `benchmarks/doclaynet/audit.py` — Read every saved case, classify evidence and create a focused review report.
- `benchmarks/doclaynet/boxes.py` — Compare native PDF text spans against DocLayNet test annotations (offline).
- `benchmarks/doclaynet/compare.py` — Compare two completed rendering runs, failing on new per-case regressions.
- `benchmarks/doclaynet/manifest.json` — Stores manifest metadata or fixtures for benchmarks/doclaynet.
- `benchmarks/doclaynet/prepare.py` — Fetch a deterministic 40-page sample using HTTP ranges, not entire archives.
- `benchmarks/doclaynet/run.py` — Offline rendering suite: no translation, OCR, vision or other model calls.
- `benchmarks/format/README.md` — Documents benchmarks/format setup and usage.
- `benchmarks/format/balanced.py` — Build a source-balanced ForMaT collection, reusing cached PDFs.
- `benchmarks/format/pilot.py` — Run a four-document ForMaT translation and source-only visual review.
- `benchmarks/format/prepare.py` — Download and validate short English ForMaT source documents for local tests.
- `benchmarks/legacy/README.md` — Documents benchmarks/legacy setup and usage.
- `benchmarks/legacy/__init__.py` — Marks this folder as a Python package; Retired repair experiments, retained for offline regression reproduction.
- `benchmarks/legacy/reasoning.py` — Retired text-only repair clients; the application does not import these providers.
- `benchmarks/legacy/settings.py` — Isolated settings for historical repair benchmarks, unused by the app.
- `benchmarks/legacy/vision.py` — Optional, bounded DeepSeek vision review through OpenRouter or DeepSeek.
- `benchmarks/legacy/vision_repair.py` — One bounded proposal per flagged region, accepted only after local validation.
- `benchmarks/openrouter/README.md` — Documents benchmarks/openrouter setup and usage.
- `benchmarks/openrouter/__init__.py` — Marks this folder as a Python package; Reusable synthetic PDF generation and translation evaluation via OpenRouter.
- `benchmarks/openrouter/cases.py` — Balanced case briefs. The model owns every layout decision.
- `benchmarks/openrouter/client.py` — Small OpenRouter client with bounded retries and persisted usage metadata.
- `benchmarks/openrouter/compare.py` — Visual comparison with validated candidate-page coordinates and rubric totals.
- `benchmarks/openrouter/generate.py` — One model call for two complete HTML documents, rendered once to PDFs.
- `benchmarks/openrouter/html_renderer.py` — Fixed Playwright runner for model-authored HTML; no model Python execution.
- `benchmarks/openrouter/prompts.py` — Defines prompts for generating document pairs and evaluating translations.
- `benchmarks/openrouter/render.py` — Locate fonts for the model-written document program.
- `benchmarks/openrouter/rubric.json` — Stores rubric metadata or fixtures for benchmarks/openrouter.
- `benchmarks/openrouter/run.py` — CLI: generate paired PDFs, translate with Studio, compare with a smaller Qwen.
- `benchmarks/ugc/README.md` — Documents benchmarks/ugc setup and usage.
- `benchmarks/ugc/__init__.py` — Marks this folder as a Python package; UGC English-to-Hindi real-document benchmark.
- `benchmarks/ugc/diagnostics.py` — Sanitized per-attempt provider diagnostics; never stores bodies or credentials.
- `benchmarks/ugc/rubric.json` — Stores rubric metadata or fixtures for benchmarks/ugc.
- `benchmarks/ugc/run.py` — Translate UGC pages sequentially while a separate process evaluates prior pages.

## Documentation

- `docs/code-walkthrough-notes.md` — Provides one-line notes for each maintained project file.
- `docs/development.md` — Documents maintenance conventions, architecture boundaries, and verification commands.
- `docs/images/initial-ui.png` — Shows the initial upload and language-selection interface in the README.
- `docs/images/review-workspace.png` — Shows a real English-to-Hindi robotics slide in the comparison interface.
- `docs/implementation.md` — Explains API behavior, rendering, recovery, timing, and internal implementation details.
- `docs/layout-policy.md` — Explains when source text stays anchored or can join and reflow.
- `docs/project-flowchart.md` — Maps the full processing workflow, components, and generated artifacts.
- `docs/verification.md` — Records release checks, results, and verification limits.

## Font assets and licenses

- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/NotoSansDevanagari-VariableFont_wdth,wght.ttf` — Supplies the NotoSansDevanagari-VariableFont_wdth,wght font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/OFL.txt` — Contains third-party font usage and redistribution license terms.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/README.txt` — Documents this bundled font family and its available styles.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari-Black.ttf` — Supplies the NotoSansDevanagari-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari-Bold.ttf` — Supplies the NotoSansDevanagari-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari-ExtraBold.ttf` — Supplies the NotoSansDevanagari-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari-ExtraLight.ttf` — Supplies the NotoSansDevanagari-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari-Light.ttf` — Supplies the NotoSansDevanagari-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari-Medium.ttf` — Supplies the NotoSansDevanagari-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari-Regular.ttf` — Supplies the NotoSansDevanagari-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari-SemiBold.ttf` — Supplies the NotoSansDevanagari-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari-Thin.ttf` — Supplies the NotoSansDevanagari-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_Condensed-Black.ttf` — Supplies the NotoSansDevanagari_Condensed-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_Condensed-Bold.ttf` — Supplies the NotoSansDevanagari_Condensed-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_Condensed-ExtraBold.ttf` — Supplies the NotoSansDevanagari_Condensed-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_Condensed-ExtraLight.ttf` — Supplies the NotoSansDevanagari_Condensed-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_Condensed-Light.ttf` — Supplies the NotoSansDevanagari_Condensed-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_Condensed-Medium.ttf` — Supplies the NotoSansDevanagari_Condensed-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_Condensed-Regular.ttf` — Supplies the NotoSansDevanagari_Condensed-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_Condensed-SemiBold.ttf` — Supplies the NotoSansDevanagari_Condensed-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_Condensed-Thin.ttf` — Supplies the NotoSansDevanagari_Condensed-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_ExtraCondensed-Black.ttf` — Supplies the NotoSansDevanagari_ExtraCondensed-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_ExtraCondensed-Bold.ttf` — Supplies the NotoSansDevanagari_ExtraCondensed-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_ExtraCondensed-ExtraBold.ttf` — Supplies the NotoSansDevanagari_ExtraCondensed-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_ExtraCondensed-ExtraLight.ttf` — Supplies the NotoSansDevanagari_ExtraCondensed-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_ExtraCondensed-Light.ttf` — Supplies the NotoSansDevanagari_ExtraCondensed-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_ExtraCondensed-Medium.ttf` — Supplies the NotoSansDevanagari_ExtraCondensed-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_ExtraCondensed-Regular.ttf` — Supplies the NotoSansDevanagari_ExtraCondensed-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_ExtraCondensed-SemiBold.ttf` — Supplies the NotoSansDevanagari_ExtraCondensed-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_ExtraCondensed-Thin.ttf` — Supplies the NotoSansDevanagari_ExtraCondensed-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_SemiCondensed-Black.ttf` — Supplies the NotoSansDevanagari_SemiCondensed-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_SemiCondensed-Bold.ttf` — Supplies the NotoSansDevanagari_SemiCondensed-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_SemiCondensed-ExtraBold.ttf` — Supplies the NotoSansDevanagari_SemiCondensed-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_SemiCondensed-ExtraLight.ttf` — Supplies the NotoSansDevanagari_SemiCondensed-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_SemiCondensed-Light.ttf` — Supplies the NotoSansDevanagari_SemiCondensed-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_SemiCondensed-Medium.ttf` — Supplies the NotoSansDevanagari_SemiCondensed-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_SemiCondensed-Regular.ttf` — Supplies the NotoSansDevanagari_SemiCondensed-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_SemiCondensed-SemiBold.ttf` — Supplies the NotoSansDevanagari_SemiCondensed-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Devanagari/static/NotoSansDevanagari_SemiCondensed-Thin.ttf` — Supplies the NotoSansDevanagari_SemiCondensed-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/NotoSansTamil-VariableFont_wdth,wght.ttf` — Supplies the NotoSansTamil-VariableFont_wdth,wght font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/OFL.txt` — Contains third-party font usage and redistribution license terms.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/README.txt` — Documents this bundled font family and its available styles.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil-Black.ttf` — Supplies the NotoSansTamil-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil-Bold.ttf` — Supplies the NotoSansTamil-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil-ExtraBold.ttf` — Supplies the NotoSansTamil-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil-ExtraLight.ttf` — Supplies the NotoSansTamil-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil-Light.ttf` — Supplies the NotoSansTamil-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil-Medium.ttf` — Supplies the NotoSansTamil-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil-Regular.ttf` — Supplies the NotoSansTamil-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil-SemiBold.ttf` — Supplies the NotoSansTamil-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil-Thin.ttf` — Supplies the NotoSansTamil-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_Condensed-Black.ttf` — Supplies the NotoSansTamil_Condensed-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_Condensed-Bold.ttf` — Supplies the NotoSansTamil_Condensed-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_Condensed-ExtraBold.ttf` — Supplies the NotoSansTamil_Condensed-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_Condensed-ExtraLight.ttf` — Supplies the NotoSansTamil_Condensed-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_Condensed-Light.ttf` — Supplies the NotoSansTamil_Condensed-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_Condensed-Medium.ttf` — Supplies the NotoSansTamil_Condensed-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_Condensed-Regular.ttf` — Supplies the NotoSansTamil_Condensed-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_Condensed-SemiBold.ttf` — Supplies the NotoSansTamil_Condensed-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_Condensed-Thin.ttf` — Supplies the NotoSansTamil_Condensed-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_ExtraCondensed-Black.ttf` — Supplies the NotoSansTamil_ExtraCondensed-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_ExtraCondensed-Bold.ttf` — Supplies the NotoSansTamil_ExtraCondensed-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_ExtraCondensed-ExtraBold.ttf` — Supplies the NotoSansTamil_ExtraCondensed-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_ExtraCondensed-ExtraLight.ttf` — Supplies the NotoSansTamil_ExtraCondensed-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_ExtraCondensed-Light.ttf` — Supplies the NotoSansTamil_ExtraCondensed-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_ExtraCondensed-Medium.ttf` — Supplies the NotoSansTamil_ExtraCondensed-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_ExtraCondensed-Regular.ttf` — Supplies the NotoSansTamil_ExtraCondensed-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_ExtraCondensed-SemiBold.ttf` — Supplies the NotoSansTamil_ExtraCondensed-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_ExtraCondensed-Thin.ttf` — Supplies the NotoSansTamil_ExtraCondensed-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_SemiCondensed-Black.ttf` — Supplies the NotoSansTamil_SemiCondensed-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_SemiCondensed-Bold.ttf` — Supplies the NotoSansTamil_SemiCondensed-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_SemiCondensed-ExtraBold.ttf` — Supplies the NotoSansTamil_SemiCondensed-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_SemiCondensed-ExtraLight.ttf` — Supplies the NotoSansTamil_SemiCondensed-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_SemiCondensed-Light.ttf` — Supplies the NotoSansTamil_SemiCondensed-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_SemiCondensed-Medium.ttf` — Supplies the NotoSansTamil_SemiCondensed-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_SemiCondensed-Regular.ttf` — Supplies the NotoSansTamil_SemiCondensed-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_SemiCondensed-SemiBold.ttf` — Supplies the NotoSansTamil_SemiCondensed-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Tamil/static/NotoSansTamil_SemiCondensed-Thin.ttf` — Supplies the NotoSansTamil_SemiCondensed-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/NotoSansTelugu-VariableFont_wdth,wght.ttf` — Supplies the NotoSansTelugu-VariableFont_wdth,wght font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/OFL.txt` — Contains third-party font usage and redistribution license terms.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/README.txt` — Documents this bundled font family and its available styles.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu-Black.ttf` — Supplies the NotoSansTelugu-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu-Bold.ttf` — Supplies the NotoSansTelugu-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu-ExtraBold.ttf` — Supplies the NotoSansTelugu-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu-ExtraLight.ttf` — Supplies the NotoSansTelugu-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu-Light.ttf` — Supplies the NotoSansTelugu-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu-Medium.ttf` — Supplies the NotoSansTelugu-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu-Regular.ttf` — Supplies the NotoSansTelugu-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu-SemiBold.ttf` — Supplies the NotoSansTelugu-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu-Thin.ttf` — Supplies the NotoSansTelugu-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_Condensed-Black.ttf` — Supplies the NotoSansTelugu_Condensed-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_Condensed-Bold.ttf` — Supplies the NotoSansTelugu_Condensed-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_Condensed-ExtraBold.ttf` — Supplies the NotoSansTelugu_Condensed-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_Condensed-ExtraLight.ttf` — Supplies the NotoSansTelugu_Condensed-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_Condensed-Light.ttf` — Supplies the NotoSansTelugu_Condensed-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_Condensed-Medium.ttf` — Supplies the NotoSansTelugu_Condensed-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_Condensed-Regular.ttf` — Supplies the NotoSansTelugu_Condensed-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_Condensed-SemiBold.ttf` — Supplies the NotoSansTelugu_Condensed-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_Condensed-Thin.ttf` — Supplies the NotoSansTelugu_Condensed-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_ExtraCondensed-Black.ttf` — Supplies the NotoSansTelugu_ExtraCondensed-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_ExtraCondensed-Bold.ttf` — Supplies the NotoSansTelugu_ExtraCondensed-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_ExtraCondensed-ExtraBold.ttf` — Supplies the NotoSansTelugu_ExtraCondensed-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_ExtraCondensed-ExtraLight.ttf` — Supplies the NotoSansTelugu_ExtraCondensed-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_ExtraCondensed-Light.ttf` — Supplies the NotoSansTelugu_ExtraCondensed-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_ExtraCondensed-Medium.ttf` — Supplies the NotoSansTelugu_ExtraCondensed-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_ExtraCondensed-Regular.ttf` — Supplies the NotoSansTelugu_ExtraCondensed-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_ExtraCondensed-SemiBold.ttf` — Supplies the NotoSansTelugu_ExtraCondensed-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_ExtraCondensed-Thin.ttf` — Supplies the NotoSansTelugu_ExtraCondensed-Thin font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_SemiCondensed-Black.ttf` — Supplies the NotoSansTelugu_SemiCondensed-Black font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_SemiCondensed-Bold.ttf` — Supplies the NotoSansTelugu_SemiCondensed-Bold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_SemiCondensed-ExtraBold.ttf` — Supplies the NotoSansTelugu_SemiCondensed-ExtraBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_SemiCondensed-ExtraLight.ttf` — Supplies the NotoSansTelugu_SemiCondensed-ExtraLight font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_SemiCondensed-Light.ttf` — Supplies the NotoSansTelugu_SemiCondensed-Light font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_SemiCondensed-Medium.ttf` — Supplies the NotoSansTelugu_SemiCondensed-Medium font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_SemiCondensed-Regular.ttf` — Supplies the NotoSansTelugu_SemiCondensed-Regular font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_SemiCondensed-SemiBold.ttf` — Supplies the NotoSansTelugu_SemiCondensed-SemiBold font for document rendering.
- `fonts/Noto_Sans_Devanagari,Noto_Sans_Tamil,Noto_Sans_Telugu/Noto_Sans_Telugu/static/NotoSansTelugu_SemiCondensed-Thin.ttf` — Supplies the NotoSansTelugu_SemiCondensed-Thin font for document rendering.
- `fonts/supported/LICENSE` — Contains third-party font usage and redistribution license terms.
- `fonts/supported/NotoSans/NotoSans-Bold.ttf` — Supplies the NotoSans-Bold font for document rendering.
- `fonts/supported/NotoSans/NotoSans-Regular.ttf` — Supplies the NotoSans-Regular font for document rendering.
- `fonts/supported/NotoSansArabic/NotoSansArabic-Bold.ttf` — Supplies the NotoSansArabic-Bold font for document rendering.
- `fonts/supported/NotoSansArabic/NotoSansArabic-Regular.ttf` — Supplies the NotoSansArabic-Regular font for document rendering.
- `fonts/supported/NotoSansBengali/NotoSansBengali-Bold.ttf` — Supplies the NotoSansBengali-Bold font for document rendering.
- `fonts/supported/NotoSansBengali/NotoSansBengali-Regular.ttf` — Supplies the NotoSansBengali-Regular font for document rendering.
- `fonts/supported/NotoSansGujarati/NotoSansGujarati-Bold.ttf` — Supplies the NotoSansGujarati-Bold font for document rendering.
- `fonts/supported/NotoSansGujarati/NotoSansGujarati-Regular.ttf` — Supplies the NotoSansGujarati-Regular font for document rendering.
- `fonts/supported/NotoSansGurmukhi/NotoSansGurmukhi-Bold.ttf` — Supplies the NotoSansGurmukhi-Bold font for document rendering.
- `fonts/supported/NotoSansGurmukhi/NotoSansGurmukhi-Regular.ttf` — Supplies the NotoSansGurmukhi-Regular font for document rendering.
- `fonts/supported/NotoSansKannada/NotoSansKannada-Bold.ttf` — Supplies the NotoSansKannada-Bold font for document rendering.
- `fonts/supported/NotoSansKannada/NotoSansKannada-Regular.ttf` — Supplies the NotoSansKannada-Regular font for document rendering.
- `fonts/supported/NotoSansMalayalam/NotoSansMalayalam-Bold.ttf` — Supplies the NotoSansMalayalam-Bold font for document rendering.
- `fonts/supported/NotoSansMalayalam/NotoSansMalayalam-Regular.ttf` — Supplies the NotoSansMalayalam-Regular font for document rendering.
- `fonts/supported/NotoSansMeeteiMayek/NotoSansMeeteiMayek-Bold.ttf` — Supplies the NotoSansMeeteiMayek-Bold font for document rendering.
- `fonts/supported/NotoSansMeeteiMayek/NotoSansMeeteiMayek-Regular.ttf` — Supplies the NotoSansMeeteiMayek-Regular font for document rendering.
- `fonts/supported/NotoSansOlChiki/NotoSansOlChiki-Bold.ttf` — Supplies the NotoSansOlChiki-Bold font for document rendering.
- `fonts/supported/NotoSansOlChiki/NotoSansOlChiki-Regular.ttf` — Supplies the NotoSansOlChiki-Regular font for document rendering.
- `fonts/supported/NotoSansOriya/NotoSansOriya-Bold.ttf` — Supplies the NotoSansOriya-Bold font for document rendering.
- `fonts/supported/NotoSansOriya/NotoSansOriya-Regular.ttf` — Supplies the NotoSansOriya-Regular font for document rendering.
- `fonts/supported/manifest.json` — Stores manifest metadata or fixtures for fonts/supported.
