# Benchmarks and retained experiments

Keep these harnesses with the source: they make layout changes reproducible and
several are exercised by offline regression tests. The installed application
includes only `app/`; it does not import or run benchmark code. Input datasets,
generated PDFs, databases, and reports belong in ignored `data/` and `output/`.

| Suite | Purpose | External calls |
| --- | --- | --- |
| [DocLayNet](doclaynet/README.md) | Pinned digital-PDF layout checks and before/after comparison | Dataset preparation downloads files; rendering is offline |
| [Baseline](baseline/README.md) | Synthetic expansion stress tests and optional translation runs | Offline by default; live runner requires `--live` |
| [OpenRouter](openrouter/README.md) | Generate paired synthetic PDFs and evaluate translations | Paid runs require `--live`; `--dry-run` prints a plan |
| [UGC](ugc/README.md) | Compare Hindi output against a supplied official Hindi edition | Paid runs require `--live`; requires your own PDFs |
| [ForMaT](format/README.md) | Exploratory source collection and source-only visual judging | Preparation downloads PDFs; pilot requires `--live` |
| [Legacy repair](legacy/README.md) | Historical repair implementations and regression reproduction | Tests use mocks; no application integration |

Start with DocLayNet when investigating rendering regressions. The other suites
answer narrower experimental questions; their automated scores are diagnostics,
not human verification of translation accuracy. See each guide for prerequisites,
input selection, reproducibility limits, and commands.

Before retiring a suite, check imports in `tests/`, other suites, and `scripts/`
and preserve any reduced application regressions. Deleting benchmark code solely
to reduce the repository's file count loses reproducibility with little space saved.
