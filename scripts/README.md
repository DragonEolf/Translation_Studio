# Manual utilities

Run modules from the repository root with `python -m scripts.NAME`. Repeatable
offline regressions live in `tests/`; these scripts are manual diagnostics and
are not collected by the default pytest command. Generated files go into ignored
`output/` directories.

| Script | Purpose and prerequisites |
| --- | --- |
| `download_fonts` | Download missing Regular/Bold fonts and licenses; requires internet |
| `audit_layout_policy` | Audit source-span ownership in a supplied PDF directory; offline |
| `compare_scan_backgrounds` | Compare saved OCR layouts/backgrounds for a supplied document ID; offline |
| `check_all_languages` | Synthetic live multilingual translation/rendering checks; requires `--live` |
| `test_paragraph_pipeline` | Synthetic end-to-end live translation; requires `--live` |
| `test_bodhan_delimiters` | Synthetic translation-markup experiment; requires `--live` |
| `inline_layout_experiment` | Historical nested-tag experiment; helper functions are covered by tests; live runner requires `--live` |
| `test_sparse_inline` | Historical sparse-tag experiment; tests import its validators; live runner requires `--live` |
| `render_inline_results`, `render_sparse_inline_results` | Offline rendering of saved synthetic experiment responses |
| `test_resume_paragraph_live` | Manual live translation of an explicitly supplied PDF; requires `--source`, `--output`, and `--live` |
| `retry_resume_paragraph_test` | Resume an existing isolated `output/pdf/resume-live-paragraph-test` run; requires `--live`; may make paid calls |
| `test_resume_cell_layout` | Offline cell-layout replay of that same saved isolated resume run |
| `render_html_pair.cjs` | Fixed browser renderer used by the OpenRouter harness; requires Node, Playwright, and Chromium |

The historical resume runners need their own saved outputs and are not portable
fixtures. Three superseded scripts tied to a hardcoded local document ID were
removed: `compare_font_fitting`, `check_paragraph_resume_offline`, and
`test_inline_resume_offline`. Use the application regressions and DocLayNet suite
for repeatable fitting/layout checks; Git history retains the old experiments.
