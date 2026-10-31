"""Validate OCR responses and map pixel coordinates to PDF document regions."""

import pymupdf as fitz

from app.config import StudioError
from app.providers.ocr import validate_response
from app.services.ocr_overlaps import overlap_reviews


class OCRReaderAgent:
    def image(self, source, number, dpi):
        with fitz.open(source) as pdf:
            pixmap = pdf[number - 1].get_pixmap(dpi=dpi, alpha=False)
            return pixmap.tobytes("png"), pixmap.width, pixmap.height

    def blocks(
        self, document_id, number, metadata, response, pixel_width, pixel_height
    ):
        validate_response(response)
        results = []
        for index, raw in enumerate(response["blocks"]):
            if not raw["text"].strip():
                continue
            x0, y0, x1, y1 = raw["bbox_xyxy"]
            if x0 < 0 or y0 < 0 or x1 > pixel_width + 1 or y1 > pixel_height + 1:
                raise StudioError(
                    "invalid_ocr_response",
                    "OCR coordinates exceed page image boundaries",
                    502,
                )
            bbox = [
                x0 * metadata["width"] / pixel_width,
                y0 * metadata["height"] / pixel_height,
                min(x1, pixel_width) * metadata["width"] / pixel_width,
                min(y1, pixel_height) * metadata["height"] / pixel_height,
            ]
            label = raw.get("label", "Paragraph").lower()
            kind = (
                "heading"
                if "title" in label
                else "table"
                if label == "table"
                else "list_item"
                if label == "list"
                else "caption"
                if "caption" in label
                else label
                if label in {"header", "footer", "footnote"}
                else "paragraph"
            )
            # Scans provide no font metrics. This is a fitting ceiling, not a
            # font inferred from OCR paragraph/line breaks. Actual size is
            # measured against the translated text and target font by TextFitter.
            size = 24.0
            results.append(
                {
                    "id": f"{document_id}-p{number}-ocr{index}",
                    "document_id": document_id,
                    "page_number": number,
                    "type": kind,
                    "reading_order": raw.get("order", index),
                    "bbox": bbox,
                    "source_text": raw["text"],
                    "style": {
                        "font": "OCR-estimated",
                        "size": size,
                        "color": 0,
                        "bold": kind == "heading",
                        "ocr": True,
                        "complex_table": kind == "table",
                        "unsupported_ocr": label
                        in {"equation", "expression", "chart", "diagram", "image"},
                    },
                    "confidence": raw.get("conf", 0),
                }
            )
            if raw.get("polygon_xy") is not None:
                results[-1]["style"]["source_polygon"] = [
                    [
                        min(x, pixel_width) * metadata["width"] / pixel_width,
                        min(y, pixel_height) * metadata["height"] / pixel_height,
                    ]
                    for x, y in raw["polygon_xy"]
                ]
        reviews = overlap_reviews(results)
        for block in results:
            if block["id"] in reviews:
                block["style"]["ocr_overlap_with"] = reviews[block["id"]]
        return results
