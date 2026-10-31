# Retired repair experiments

These modules previously lived in `app/providers/` and `app/services/`. The
application no longer uses them; they remain here so historical behavior can be
tested and compared without confusing it with the current selected-region repair.

- `reasoning.py`: old text-only revision and issue-resolution clients.
- `vision.py`: old formatting-only vision provider and proposal schema.
- `vision_repair.py`: old synchronous candidate-layout evaluation.
- `settings.py`: settings used only by these experiments.

Their tests use mocked providers. Current repair lives in
`app/providers/repair.py` and `app/services/ai_repair.py`. Do not wire legacy clients
into the automatic translation pipeline.
