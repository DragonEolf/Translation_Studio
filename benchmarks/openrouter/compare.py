"""Visual comparison with validated candidate-page coordinates and rubric totals."""
import base64
import hashlib
import json
from pathlib import Path

import pymupdf as fitz
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .prompts import JUDGE


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    max_points: float = Field(gt=0, le=100)
    description: str = Field(min_length=1)


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: list[Criterion] = Field(min_length=1, max_length=20)

    @field_validator("criteria")
    @classmethod
    def valid_total(cls, criteria):
        if len({c.id for c in criteria}) != len(criteria):
            raise ValueError("Duplicate rubric criteria")
        if abs(sum(c.max_points for c in criteria)-100) > 0.001:
            raise ValueError("Rubric points must sum to 100")
        return criteria


class Earned(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: str
    points: float = Field(ge=0, le=100, allow_inf_nan=False)


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region_id: str
    criterion: str
    improvement: str = Field(min_length=1, max_length=200)

    @field_validator("improvement")
    @classmethod
    def single_line(cls, value):
        if not value.strip() or any(c in value for c in "\r\n\u2028\u2029"):
            raise ValueError("Improvement must be one nonempty line")
        return value


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: list[Earned]
    issues: list[Region] = Field(max_length=50)


def fingerprint(paths, model, rubric):
    digest = hashlib.sha256((model + JUDGE + rubric.model_dump_json()).encode())
    for path in paths:
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def compare(router, source, reference, candidate, language, rubric, model, output):
    content = [{"type": "text", "text": json.dumps({
        "target_language": language, "rubric": rubric.model_dump(),
        "coordinate_system": "candidate PDF points, top-left origin; page numbers start at 1"})}]
    dimensions = []
    regions = {}
    for label, path in [("SOURCE", source), ("REFERENCE", reference), ("CANDIDATE", candidate)]:
        with fitz.open(path) as pdf:
            if not 1 <= len(pdf) <= 12:
                raise ValueError("Comparer accepts 1-12 pages per document; no pages are silently skipped")
            for number, page in enumerate(pdf, 1):
                dims = [page.rect.width, page.rect.height]
                if label == "CANDIDATE":
                    dimensions.append(dims)
                    page_regions = []
                    for index, block in enumerate(page.get_text("blocks")):
                        box = fitz.Rect(block[:4]) & page.rect
                        if box.is_empty or block[6] != 0:
                            continue
                        rid = f"p{number}-text{index+1}"
                        entry = dict(region_id=rid, page=number, bbox=list(box), text=block[4])
                        regions[rid] = entry
                        page_regions.append(entry)
                    for row in range(4):
                        for col in range(2):
                            rid = f"p{number}-grid{row*2+col+1}"
                            entry = dict(region_id=rid, page=number, bbox=[
                                col*dims[0]/2, row*dims[1]/4,
                                (col+1)*dims[0]/2, (row+1)*dims[1]/4])
                            regions[rid] = entry
                            page_regions.append(entry)
                    content.append({"type": "text", "text": json.dumps(
                        {"candidate_regions": page_regions}, ensure_ascii=False)})
                    # Overlay measured text-region IDs for visual grounding. Original PDF is untouched.
                    for entry in page_regions:
                        if "text" not in entry:
                            continue
                        box = fitz.Rect(entry["bbox"])
                        page.draw_rect(box, color=(0.1, 0.3, 0.9), width=0.4, overlay=True)
                        page.insert_text((box.x0, max(7, box.y0-2)), entry["region_id"],
                                         fontsize=6, color=(0.1, 0.3, 0.9), overlay=True)
                # Send actual rasterized PDF pages, not only their text or JSON recipe.
                png = page.get_pixmap(dpi=120).tobytes("png")
                content.extend([{"type": "text", "text": json.dumps({
                    "document": label, "page": number, "pdf_dimensions_points": dims,
                    "image_dimensions_pixels": [round(dims[0]*120/72), round(dims[1]*120/72)],
                    "extracted_text": page.get_text()[:30000]}, ensure_ascii=False)},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,"+
                     base64.b64encode(png).decode("ascii")}}])
    answer, usage = router.complete(model, JUDGE, content, Assessment.model_json_schema(), 6000)
    # Keep the raw response even when semantic validation rejects it.
    (output / "comparison-response.json").write_text(json.dumps({"response": answer, **usage}, ensure_ascii=False, indent=2))
    assessment = Assessment.model_validate_json(answer)
    maxima = {c.id: c.max_points for c in rubric.criteria}
    if len(assessment.criteria) != len(maxima) or {c.criterion for c in assessment.criteria} != maxima.keys():
        raise ValueError("Comparer returned missing or repeated rubric criteria")
    for criterion in assessment.criteria:
        if criterion.points > maxima[criterion.criterion]:
            raise ValueError("Comparer exceeded rubric maximum")
    issues = []
    for issue in assessment.issues:
        if issue.criterion not in maxima or issue.region_id not in regions:
            raise ValueError("Comparer referenced an unknown criterion or candidate region")
        region = regions[issue.region_id]
        issues.append(dict(**issue.model_dump(), page=region["page"], bbox=region["bbox"]))
    result = dict(score=round(sum(c.points for c in assessment.criteria), 2),
                  criteria=[c.model_dump() for c in assessment.criteria], issues=issues,
                  rubric=rubric.model_dump(), model=usage["model"],
                  usage=usage["usage"],
                  fingerprint=fingerprint([source, reference, candidate], model, rubric),
                  bbox_units="PDF points", bbox_origin="top-left", bbox_document="candidate.pdf",
                  assessment_type="automated, not human-validated")
    (output / "evaluation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    with fitz.open(candidate) as pdf:
        for issue in issues:
            annot = pdf[issue["page"]-1].add_rect_annot(fitz.Rect(issue["bbox"]))
            annot.set_colors(stroke=(1, 0, 0))
            annot.set_info(content=f"{issue['criterion']}: {issue['improvement']}")
            annot.update()
        pdf.save(output / "annotated.pdf")
    return result
