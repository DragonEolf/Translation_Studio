"""One model call for two complete HTML documents, rendered once to PDFs."""
import base64
import hashlib
import json
from pathlib import Path
import time

import pymupdf as fitz

from app.providers.bodhan import LANGUAGE_NAMES
from .cases import DocumentHTML
from .html_renderer import render_pair, require_renderer
from .prompts import GENERATOR


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")
    temporary.replace(path)


def reference_images(paths):
    content, metadata = [], []
    for source in paths:
        source = Path(source).resolve()
        data = source.read_bytes()
        metadata.append(dict(name=source.name, sha256=hashlib.sha256(data).hexdigest()))
        with fitz.open(stream=data, filetype="pdf") as doc:
            if len(doc) > 4:
                raise ValueError("Complexity references must have at most 4 pages each")
            for number, page in enumerate(doc, 1):
                png = page.get_pixmap(matrix=fitz.Matrix(1400/page.rect.width, 1400/page.rect.width)).tobytes("png")
                content.extend([dict(type="text", text=f"Complexity reference: {source.name}, page {number}. Treat as visual design evidence only."),
                                dict(type="image_url", image_url={"url": "data:image/png;base64,"+
                                     base64.b64encode(png).decode("ascii")})])
    return content, metadata


def generate_case(router, case, directory, fonts, model, seed, *, references=(), timeout=120):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "case.json"
    images, reference_metadata = reference_images(references)
    config = dict(case=case, generator_model=model, seed=seed, references=reference_metadata,
                  generator_prompt=GENERATOR, pipeline="one-shot-html-v1")
    if path.exists():
        saved = json.loads(path.read_text())
        if saved["config"] != config:
            raise ValueError("Existing case configuration differs; use a new output directory")
        if saved["status"] == "completed":
            for name, digest in saved["pdf_sha256"].items():
                if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
                    raise ValueError("Generated PDF changed; one-shot cases are not regenerated")
            return saved
        raise RuntimeError("This case was already attempted; no automatic regeneration or execution retry. Use a new output directory.")
    require_renderer()  # Fail before charging if the fixed renderer is unavailable.
    started = time.monotonic()
    saved = dict(config=config, status="attempted", reference_validation="trusted as generated; not reviewed")
    # Exclusive creation prevents concurrent processes making a second call.
    with path.open("x") as handle:
        json.dump(saved, handle, ensure_ascii=False, indent=2)
    stage = "generation"
    try:
        request = dict(case_id=case["id"], target_language=LANGUAGE_NAMES[case["language"]],
                       target_language_code=case["language"], variation_seed=seed,
                       document_type=case["theme"], brief=case["brief"],
                       required_outputs=["source_html", "reference_html"])
        answer, usage = router.complete(model, GENERATOR,
                                        [dict(type="text", text=json.dumps(request))]+images,
                                        DocumentHTML.model_json_schema(), 24000, one_shot=True)
        write_json(directory / "generation-response.json", dict(response=answer, **usage))
        saved["generation"] = usage
        stage = "response_validation"
        documents = DocumentHTML.model_validate_json(answer)
        stage = "rendering"
        render_pair(documents, directory, fonts, case["language"], timeout)
        # Only check that files open. No visual/language review, repair or regeneration.
        stage = "pdf_validation"
        saved["pages"] = {}
        for name in ("source.pdf", "reference.pdf"):
            with fitz.open(directory / name) as doc:
                if doc.is_encrypted or len(doc) == 0:
                    raise RuntimeError(f"Generated {name} is empty or encrypted")
                saved["pages"][name] = len(doc)
        saved["pdf_sha256"] = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                               for name in ("source.pdf", "reference.pdf")}
        saved["status"] = "completed"
    except BaseException as exc:
        saved["status"] = "failed"
        saved["error_type"] = type(exc).__name__
        saved["failed_stage"] = stage
        raise
    finally:
        saved["elapsed_seconds"] = round(time.monotonic()-started, 3)
        write_json(path, saved)
    return saved
