# Native PDF bounding-box baseline

Run from the repository root after preparing the existing 40-page sample:

```bash
.venv/bin/python -m benchmarks.doclaynet.boxes --out output/benchmarks/doclaynet-boxes-new
.venv/bin/python -m pytest -q tests/test_doclaynet_boxes.py
```

The command checks the pinned annotations and PDF checksums, runs the actual TextReaderAgent and BlockMakerAgent, scales PDF coordinates to the 1025 × 1025 annotation coordinates, and writes per-page boxes, results, and overlays. Green rectangles are DocLayNet text-region annotations; red rectangles are extracted spans. Output directories are never overwritten.

On 2026-10-29 the 40 selected DocLayNet test pages produced 718 text-region references and 7,148 spans. At IoU 0.5, class-agnostic greedy one-to-one matching found 320 matches (44.6% region recall; 4.5% span precision); at 0.75 it found 236 (32.9%; 3.3%). These are diagnostic granularity comparisons, not standard DocLayNet detector AP: a paragraph reference often contains many predicted spans. Predictions include text in tables and other excluded annotation classes, which further depresses precision. No category classification accuracy was measured.

The union of span rectangles within each reference covers an average of 82.7% of the reference rectangle; 693 of 718 (96.5%) have at least 50% area coverage. This is an unweighted average across regions, and coverage is geometric: blank space, line spacing, overlapping text and boxes can affect it. It does not establish that words were extracted correctly or completely. All references had at least 10% coverage. Counts per label and detailed box data are in summary.json.

Sample selection deliberately covers rare labels and distinct documents, so it is not a random or representative estimate. It only assesses native PDF extraction: there were no OCR calls or translated/rendered-output evaluations. The annotations are drawn on squared images and PDF x/y scaling is independent. Inspect overlays before interpreting individual errors. For example, page 2378 shows word-level fragmentation inside multi-line annotations and has 58.0% mean area coverage; much of the difference is white space between lines and words rather than proof of missing text.

Future work: compare against a separately defined paragraph grouping, add character-level completeness based on PDF text with manual checks, evaluate table and picture detection using appropriate region predictions, and run OCR separately on the original DocLayNet PNGs. Keep this sample fixed for regressions, with a separate holdout for unbiased evaluation after tuning.

## Comparison of all digital layout modes

Run separately with `--mode spans`, `--mode auto`, and `--mode paragraphs`, each with a unique `--out` directory. All three use the same extracted source and the production `group_blocks` implementation. The box evaluated is the source/group layout box before translation and fitting.

| Mode | Boxes | Matches at IoU 0.5 | Matches at IoU 0.75 | Mean region area covered | Regions at least 50% covered |
|---|---:|---:|---:|---:|---:|
| spans | 7,148 | 320 / 718 (44.6%) | 236 / 718 (32.9%) | 82.7% | 693 / 718 |
| auto | 3,294 | 458 / 718 (63.8%) | 352 / 718 (49.0%) | 87.4% | 702 / 718 |
| paragraphs | 2,550 | 556 / 718 (77.4%) | 427 / 718 (59.5%) | 91.9% | 710 / 718 |

Paragraph mode improved the number of IoU 0.5 matches on 38 pages, tied on two, and worsened on none. This reflects closer agreement with annotation-region granularity, not proven improved extraction or final translated layout. Larger grouped rectangles include internal whitespace, mechanically raising the geometric coverage statistic, and can encompass unrelated material. One-to-one scores are class-agnostic; predictions inside tables and other excluded regions remain in the precision denominator. This non-random 40-page diagnostic is not a standard object-detection benchmark. See the per-mode summaries and page overlays for detailed inspection.
