# Version checkpoints

## `checkpoint/transliteration-layout-2026-10-29`

Translation workflow and layout checkpoint, including the current supporting
OCR, rendering, benchmark, and regression-test work:

- Optional automatic Bodhan transliteration between translation and final PDF generation.
- Reasoning and vision repairs disabled in the application; legacy benchmark code isolated.
- Shared table-cell space with readable font limits and selective neighboring-paragraph reflow.
- Source paragraph grouping before translation, strict XML validation, and bounded recovery.
- Before/after layout diagnostics, translation-retention tracking, and rollback of harmful reflow.
- Stronger SQLite references, indexes, uniqueness rules, relationships, and validation.
- Updated responsive interface with IIT Madras branding and credit to Krishna Karthikeyan.

Validation: 289 Python tests and all browser checks passed. A final targeted
65-test run also passed after including missing translations in outcome counts.
All 47 historical overflow cases have not yet been individually verified.

Uploaded PDFs, generated outputs, the local database, and credentials are excluded.

## `checkpoint/span-rendering-2026-10-17`

Working version before experimenting with paragraph layout and inline formatting:

- Selectable PDF text retains individual span boxes and formatting.
- Bodhan translations use validated XML batching with smaller-batch fallback.
- Caching is disabled by default.
- The local upload/download interface supports all 26 language/script options.
- Downloads use the original filename and target language.
- Noto fonts, tests, and experiment scripts are included.

Uploaded documents, generated output, the local database, and credentials remain
local and are excluded from Git.

To inspect or run this checkpoint in a separate checkout without changing ongoing
work:

```sh
git worktree add ../Translation_Studio-checkpoint checkpoint/span-rendering-2026-10-17
```

The checkpoint does not include paragraph reflow or translated inline formatting
tags. The shared-size comparison script is an experiment, not production behavior.
