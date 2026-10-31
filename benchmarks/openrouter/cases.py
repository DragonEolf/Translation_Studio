"""Balanced case briefs. The model owns every layout decision."""

from pydantic import BaseModel, ConfigDict, Field


class DocumentHTML(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_html: str = Field(min_length=1, max_length=250000)
    reference_html: str = Field(min_length=1, max_length=250000)


BRIEFS = [
    (
        "dense technical resume",
        "One tightly packed page: education table, experience and projects, narrow date/role columns, inline emphasis, links, bullets and colored section bands.",
    ),
    (
        "hardware setup and safety guide",
        "Two landscape pages: multiple columns, original vector device diagrams, numbered callouts, warnings, setup steps and a dense support-contact table.",
    ),
    (
        "research engineer resume",
        "One dense page: compact academic history, publications, research projects, nested bullet points, technical skills and sidebar metadata.",
    ),
    (
        "industrial equipment service manual",
        "Two pages: schematic diagram with a legend, numbered procedures, safety boxes, specification tables and small footer notes.",
    ),
    (
        "academic programme application",
        "Two dense pages: merged table headers, filled form fields, qualifications, side notes, checklist, deadlines and signature area.",
    ),
    (
        "financial report",
        "Two pages: narrow columns, multi-level financial tables, totals, original vector chart with labels, explanatory footnotes and running headers.",
    ),
    (
        "conference programme",
        "Two pages: multi-track schedule grid with spanning sessions, speaker biographies, room diagram and registration notes.",
    ),
    (
        "technical product datasheet",
        "Two pages: original vector product illustration, callout labels, dimensions, comparison tables, legends and fine-print specifications.",
    ),
    (
        "project portfolio resume",
        "One tightly packed page: narrow project labels, wide dense descriptions, inline bold/italic runs, metrics, links and ruled section boundaries.",
    ),
    (
        "public service information booklet",
        "Two pages: three-column text, decision-flow diagram, eligibility matrix, warning panels, multilingual-style labels and numbered footnotes.",
    ),
]


def plan(count, languages):
    if count < 1 or not languages or len(set(languages)) != len(languages):
        raise ValueError("Use a positive count and distinct languages")
    if count % len(languages):
        raise ValueError(
            "Count must be divisible by the number of languages for equal coverage"
        )
    return [
        dict(
            id=f"case-{i + 1:05d}-{languages[i % len(languages)]}",
            language=languages[i % len(languages)],
            theme=BRIEFS[(i // len(languages)) % len(BRIEFS)][0],
            brief=BRIEFS[(i // len(languages)) % len(BRIEFS)][1],
        )
        for i in range(count)
    ]
