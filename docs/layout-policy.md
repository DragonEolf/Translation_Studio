# Layout decisions and measured paragraph flow

Translation Studio uses a deterministic policy to distinguish sentence flow from
position-dependent text. PDF extraction blocks are provenance, not reliable
paragraph boundaries. The policy inspects source geometry before translation;
it does not concatenate unrelated model outputs after translation.

## Decision boundaries

| Source structure | Default treatment |
| --- | --- |
| Body prose with aligned, closely spaced continuation lines | Join into a source paragraph; retain every source span and style reference. |
| Short final line after a full prose line | Include it when continuation geometry is consistent. |
| Blank paragraph gap or changed indentation | Keep separate paragraphs. |
| Resume heading, employer/title/date row, contact details, skills row | Preserve independent regions. |
| New bullet or numbered item | Start a new item; a compatible wrapped continuation can stay with that item. |
| Table | Keep cells separate; existing cell fitting applies within a cell. |
| Multiple columns | Keep columns separate; another region inside a proposed union vetoes the join. |
| Form field, short label, equation/code region, caption, footnote, header/footer | Preserve independent placement. |
| Image or rule between text regions | Treat as a boundary; existing source backdrops and owned decorations are distinguished from intervening obstacles. |
| OCR/scanned page | Keep the existing OCR reconstruction path; do not infer digital paragraphs from estimated OCR boxes. |
| Ambiguous source geometry | Preserve source regions rather than force a merge. |
| Preserve original layout (`spans`) | Bypass paragraph reconstruction; keep the original span path. |

Cross-block continuation requires compatible body roles and sizes, matching left
margins, a source line gap no greater than 0.8 times the body font size, and evidence
that the preceding line fills its column. Short complete statements, field labels,
contacts and explicit separators prevent joining. These are conservative heuristics,
not guarantees of semantic paragraph recognition for every PDF.

Automatic mode can group confirmed multiline prose even when it has fewer than
four source spans. Short single-line regions retain the earlier span-count rule;
tables retain their separate cell path. Existing native source-block grouping is
retained for compatibility.

## Geometry and exact text measurement

Confirmed prose can use otherwise empty horizontal column space and vertical
space before the next source obstacle. Its source rectangles remain stored for
redaction and provenance. The allocation cannot cross another region, column,
cell, visible rule, image or page boundary. New cross-block prose receives more
line spacing than fixed-span text; fitting, measurements and final rendering use
that same setting.

Measuring isolated characters is insufficient for Indic scripts. Candidate
layouts use PyMuPDF's shaped HTML text, including the actual font, glyph fallback,
inline font-size ratios, bold/italic runs, direction and line spacing. This is the
same markup used for the final PDF, and measurements are cached within a pass.
No AI call is needed for these decisions.

## Joint paragraph optimization

Compatible neighboring prose paragraphs may share an obstacle-free column zone.
Their translations remain separate and paragraph breaks are retained. A bounded
dynamic-programming pass compares measured font-scale/height candidates. Its cost
penalizes:

- Relative shrinkage from the original body font size.
- Differences in relative scale between neighboring paragraphs.
- Vertical displacement from source paragraph positions.

The current weights are 12, 24 and 0.02 respectively, with logarithmic scale
penalties and displacement normalized by source font size. Each candidate must
fit inside the zone using its exact measured height. The pass makes at most 96
measurements per page, caches repeated measurements, and retains at most 128
states per paragraph. Fitted readable neighbors cannot lose more than 10% of
their current size in this pass. If no better feasible arrangement exists, the
previous layout remains.

The output is rendered and checked before acceptance. New per-region checker
errors or loss of a previously fitted translation cause rollback. Font-size
warnings remain visible when safe repair is not possible. Passing these checks
is evidence about rendering, not translation accuracy.

## Saved-document compatibility

Fresh translation reconstructs source paragraphs before calling the provider.
A render-only request cannot create a new sentence-level translation from saved
line fragments. When a new cross-block paragraph lacks a saved paragraph
translation, existing child regions and their available translations remain in
place. A fresh translation request enables the new grouping. This also protects
partially translated saved documents.

## Audit and regression coverage

Each grouped region stores a `layout_policy` decision, reason, source block/line
counts, original source box, and allocated flow bounds where applicable.
`layout-report.json` includes that decision and the column solver's measurement
budget, attempted changes and acceptance result. Every source span must occur
exactly once in grouping; loss or duplication fails explicitly.

Regression tests cover prose, short tails, resumes, bullets, columns, tables,
forms, equations, inline styles/literals, rules/images, mode switching, saved
translations, measurement budgets, readable-neighbor protection and Tamil shaping.
See `tests/test_layout_policy.py`, `tests/test_column_flow.py`, and the existing
layout/OCR/typography tests.

Broad offline checks use public benchmark pages and fixed synthetic expansion,
with no translation or vision calls. Their purpose is to expose regressions, not
to claim universal PDF or linguistic correctness.
