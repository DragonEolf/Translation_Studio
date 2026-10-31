# Development and maintenance

For installation and usage, start with the [README](../README.md). For rendering
rules, see [layout policy](layout-policy.md) and [implementation notes](implementation.md).

## Checks

From the repository root, with Python 3.12 or newer:

```sh
python -m pip install -e '.[test,dev]'
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

Use `python -m ruff check --fix .` for safe lint fixes and
`python -m ruff format .` for formatting. Ruff is pinned in the development extra
so contributors use the same formatting rules. It checks imports, unused bindings,
undefined names, and basic statement structure. Python tests use mocks and temporary
data; they do not call paid providers. Frontend verification instructions are in
the [browser test guide](../tests/web/README.md). Frontend files use Prettier's
default rules, pinned here to the version used for this cleanup:

```sh
npx --yes prettier@3.9.10 --check 'app/web/*.{js,css,html}' scripts/render_html_pair.cjs tests/web/studio.spec.cjs
```

Replace `--check` with `--write` to format those files. This optional check needs
Node.js; the application itself still has no frontend build step.

## Where changes belong

- `app/main.py` validates API requests and submits work; keep long-running work in jobs.
- `app/orchestrator.py` coordinates stages and persists restartable progress.
- `app/agents/` handles extraction, grouping, translation, fitting, and checks.
- `app/providers/` owns external API calls and bounded retries.
- `app/services/` owns local rendering, layout, review, storage, and cancellation.
- `app/models/` owns persistent state and SQLite integrity rules.
- `tests/` contains repeatable regressions; `scripts/` contains manual diagnostics.
- `benchmarks/` contains optional evaluations, including isolated legacy experiments.

PyMuPDF operations in the running app must use `pdf_thread` from the orchestrator:
its lock serializes PDF access across jobs. Provider calls stay asynchronous.
Run one application process per data directory. The CLI must not process a
document while the server has an active job for it.

Preserve source-span ownership, translation revisions, protected literals, and
rollback checks when changing layout behavior. Use a reduced synthetic regression
for a confirmed bug and an offline before/after benchmark for broader layout changes.
Keep production uploads, `.env`, databases, generated outputs, and provider responses
out of Git. Keep the licenses with bundled fonts.

## Remaining structural work

The API factory, orchestration stages, and rich-text grouping remain substantial
functions. Future feature work should extract cohesive operations with existing
regressions as protection. Avoid a broad rewrite merely to reduce line counts.
Formatting and passing tests improve maintainability; they do not prove the absence
of bugs or establish translation accuracy.
