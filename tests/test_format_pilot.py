import json
import sys

import pymupdf as fitz
import pytest

from benchmarks.format.pilot import judge, main
from benchmarks.openrouter.compare import Rubric


class FakeRouter:
    def __init__(self, issue="p1-grid1"):
        self.issue = issue

    def complete(self, model, prompt, content, schema, max_tokens):
        assert "No reference translation is available" in prompt
        assert sum(x["type"] == "image_url" for x in content) == 2
        return json.dumps(
            {
                "criteria": [{"criterion": "layout", "points": 80}],
                "issues": [
                    {
                        "criterion": "layout",
                        "region_id": self.issue,
                        "improvement": "Check this area",
                    }
                ],
            }
        ), {"model": model, "usage": {}}


def test_pilot_requires_live_before_reading_inputs_or_creating_output(
    tmp_path, monkeypatch
):
    output = tmp_path / "pilot"
    monkeypatch.setattr(sys, "argv", ["pilot", "--out", str(output)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not output.exists()


def test_judge_saves_localized_findings_and_rejects_unknown_region(tmp_path):
    for name in ("source", "candidate"):
        with fitz.open() as pdf:
            p = pdf.new_page()
            p.insert_text((20, 30), "example")
            pdf.save(tmp_path / f"{name}.pdf")
    rubric = Rubric.model_validate(
        {"criteria": [{"id": "layout", "max_points": 100, "description": "layout"}]}
    )
    result = judge(FakeRouter(), tmp_path, rubric, "fake")
    assert result["issues"][0]["page"] == 1
    assert len(result["issues"][0]["bbox"]) == 4
    with pytest.raises(ValueError, match="Invalid issue"):
        judge(FakeRouter("nonexistent"), tmp_path, rubric, "fake")
