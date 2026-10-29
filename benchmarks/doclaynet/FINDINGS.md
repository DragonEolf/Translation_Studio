# How the first DocLayNet run is being used

The original run at `output/benchmarks/doclaynet40` is immutable evidence. It
contains 240 cases, 14 with no automated findings and 226 needing review.
That is not an accuracy score: any warning marks a case for review, and the
checker itself had defects. No translation quality is measured.

## Confirmed defects reduced to regression tests

1. **Dropped word separators.** PDF spaces can be standalone spans. Block
   extraction omits whitespace-only blocks; paragraph grouping used to concatenate
   surviving spans without recovering those separators. The original auto cases
   have 2,074 such alphanumeric boundaries across 179 groups on 18 pages. Grouping
   now recovers only actual intervening whitespace, preserving style splits inside
   words. Page 01 is a visual acceptance fixture.
2. **Searchable text replaced by its placeholder.** The outlined-glyph renderer
   adds an invisible character wrapped in `/ActualText`. With MuPDF 1.28.2, a
   replacement ending with that same character can extract as just the invisible
   character. A tiny standalone PDF reproduces it. The anchor now differs from
   the replacement suffix. Tests cover both possible anchors and trailing space.
3. **Ligatures reported as missing.** `ﬁ`/`ﬀ` can extract as `fi`/`ff` without
   content loss. Comparison now folds the seven Latin presentation ligatures.
   It deliberately does not apply blanket NFKC, which would equate meaningful
   mathematical distinctions such as superscript digits.
4. **Lost bold weight despite an explicit font name.** On page 34,
   `FranklinGothic-DemiCond` has no bold flag. Explicit style suffixes now augment
   PDF flags. This recovers weight, not the exact original font family, condensed
   width, transparency or other complex appearance attributes.

## Measurement and fixture corrections

- All original identity-mode collision reports already overlap in initial
  extracted/group boxes (913 span, 458 paragraph, 612 auto). The checker now
  distinguishes `source_geometry_overlap` warnings from newly increased overlap
  errors. Initial grouping geometry is not necessarily harmless; it remains
  visible for review and cannot justify shrinking all overlapping boxes.
- The pixel-change mask now includes actual approved source redaction regions,
  including source regions larger than their visible ink boxes.
- Synthetic expansion used to protect each span separately. Codes split by
  style boundaries could be changed by the fixture itself. Complete line-level
  literals are now protected before splitting replacement strings into spans.
  This changes the fixture version and must be disclosed in comparisons.
- Below-6pt totals mix prose, numeric table values and punctuation. They also
  count text that was already smaller than 6pt. The harness records those roles
  and the original-size floor separately. Numeric values remain important for
  readability; these categories are not exemptions from review.

## Remaining limits: preserve, isolate and investigate

| Original category | Evidence from all original cases | Decision |
|---|---|---|
| `overflow` | 56 findings, all punctuation/symbol-only; 4 pages | Keep source and warning. Fixed glyph boxes can be too small for substituted HTML line boxes. Do not expand into neighbours or impose a global font floor. Use page 08's 9.96pt → 1.83pt full stop as a reduced geometry fixture. |
| `unsupported_layout` | 54 findings; all rotated/vertical text; 9 pages | Source preservation is intentional until rotation-aware rendering is implemented and tested. |
| `unsupported_glyphs` | 604 findings: 180 control-character cases, 12 private-use cases, 412 other coverage cases; 10 pages | Separate damaged/unmapped source extraction from missing target fonts. Keep original glyphs and review issues. Selective OCR would be a separate, potentially billable operation; this suite does not enable it. |
| `small_text` | 8,480 findings across the six configurations | Investigate real words/table values and marker fragments separately. Source-size-relative warnings remain. A fitter can succeed at an unreadable size; successful fitting is not the acceptance criterion. |
| `font_size_inconsistent` | 4,592 findings | Retain role/cell-specific relative-size checks. No global equalization or forced minimum that destroys hierarchy. |
| `graphics_changed` | 12 findings on pages 34 and 36 | Page 34's JPEG bytes and image transform are unchanged; photo raster drift is small but the warning is retained. Page 36's residual region is around an equation. Neither is an automatic license to weaken graphics checks. |
| Remaining `missing_text` / `protected_value_changed` | Reassess after the confirmed extraction and fixture fixes | Preserve hard checks. Do not excuse a local failure merely because the same text exists elsewhere on the page. |

Each remaining category now has explicit evidence, a conservative behavior, and a
small follow-up target. Removing warnings is not the goal; preserving readable
content and making actual defects reproducible is.

## Verification

The focused regressions and existing Python suite passed (216 tests, optional
vision review disabled). The reviewed matrix is written into
`output/benchmarks/doclaynet40-reviewed`, leaving the initial PDFs and logs intact.
Its `audit.json` and `review.html` provide the final inventories and filtered
before/after evidence after all 240 cases complete. Source hashes are captured at
run start because other work in the shared workspace may proceed concurrently.

### Completed reviewed run

All 240 cases completed without execution errors. All 2,074 identified dropped
separator pairs are present in the corrected auto-mode paragraph input; the
pair check is saved in `acceptance.json`. The page 01 PDF was visually checked.

| Finding | Original | Reviewed | Interpretation |
|---|---:|---:|---|
| Missing text | 463 | 0 | Baseline review found 95 placeholder suffix failures, 201 cases equivalent after Latin ligature folding, and 167 other local extraction mismatches. The revised render/check combination has none. |
| Protected values | 8 | 0 | Corrected whitespace and span-crossing synthetic fixture protection. This is not a score of model translation quality. |
| Collision errors | 3,966 | 0 | All 3,966 remain visible as inherited-geometry warnings, not repaired/discarded overlaps. |
| Small-text warnings | 8,480 | 8,482 | A net increase remains; restored spaces take real width. |
| Size-consistency warnings | 4,592 | 4,611 | A net increase remains; evaluate local typography rather than hiding these warnings. |
| Overflow | 56 | 56 | Punctuation/symbol limitations remain. |
| Unsupported glyphs | 604 | 604 | Original source remains preserved. |
| Rotated/vertical layouts | 54 | 54 | Original source remains preserved. |
| Graphics changes | 12 | 12 | Remain open for appearance review. |

The baseline comparator flags six page/profile/mode cases with increased counts
of fitted blocks below 6pt. Inspection traces the threshold crossings to restored
spacing: page 16's expanded `Box around the text elements` (6.335 → 5.974pt),
page 17's classification codes (6 → 5.88pt), and page 24's mathematical fragments.
These are recorded in `regressions.json`, not waived or silently accepted.

Page 34 also exposes an appearance limitation beyond the recovered bold weight:
the source's watermark uses an ExtGState opacity of approximately 0.21, while
PyMuPDF's extracted span/trace reports it as fully opaque. Its output remains too
dark. Correct support requires reliable graphics-state-to-text association;
guessing opacity from a screenshot or changing all grey text would be unsafe.

The appropriate next rendering work is bounded line-box handling for the specific
readability regressions, and a separate treatment of mathematical/transparent
text. The current changes do not claim those problems are fixed.
