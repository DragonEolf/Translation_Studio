"""Bodhan OCR client and strict validation of text and page-image coordinates."""

import asyncio
import base64
import math
from typing import Protocol

import httpx

from app.config import StudioError
from app.services.timing import timed_span


class OCRProvider(Protocol):
    async def extract(self, png: bytes) -> dict: ...


def _simple_polygon(polygon):
    if len({tuple(p) for p in polygon}) != len(polygon):
        return False

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a, b, c):
        return (
            cross(a, b, c) == 0
            and min(a[0], b[0]) <= c[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
        )

    edges = list(zip(polygon, polygon[1:] + polygon[:1]))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 1, len(edges)):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            c, d = edges[j]
            if (
                cross(a, b, c) * cross(a, b, d) < 0
                and cross(c, d, a) * cross(c, d, b) < 0
            ) or any(
                (
                    on_segment(a, b, c),
                    on_segment(a, b, d),
                    on_segment(c, d, a),
                    on_segment(c, d, b),
                )
            ):
                return False
    return True


def validate_response(body):
    try:
        if body["choices"][0]["finish_reason"] != "stop" or body.get("truncated"):
            raise ValueError()
        blocks = body["blocks"]
        if not isinstance(blocks, list):
            raise ValueError()
        for block in blocks:
            if not isinstance(block.get("order", 0), int) or not isinstance(
                block.get("label", "Paragraph"), str
            ):
                raise ValueError()
            bbox = block["bbox_xyxy"]
            if len(bbox) != 4 or not all(
                isinstance(v, (float, int)) and math.isfinite(v) for v in bbox
            ):
                raise ValueError()
            if (
                bbox[2] <= bbox[0]
                or bbox[3] <= bbox[1]
                or not isinstance(block["text"], str)
            ):
                raise ValueError()
            polygon = block.get("polygon_xy")
            if polygon is not None:
                if not isinstance(polygon, list) or not 3 <= len(polygon) <= 256:
                    raise ValueError()
                for point in polygon:
                    if (
                        not isinstance(point, (list, tuple))
                        or len(point) != 2
                        or not all(
                            isinstance(v, (int, float)) and math.isfinite(v)
                            for v in point
                        )
                    ):
                        raise ValueError()
                    if (
                        not bbox[0] <= point[0] <= bbox[2]
                        or not bbox[1] <= point[1] <= bbox[3]
                    ):
                        raise ValueError()
                area = sum(
                    a[0] * b[1] - b[0] * a[1]
                    for a, b in zip(polygon, polygon[1:] + polygon[:1])
                )
                if abs(area) < 1e-6 or not _simple_polygon(polygon):
                    raise ValueError()
            confidence = block.get("conf", 0)
            if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                raise ValueError()
        return body
    except (KeyError, IndexError, TypeError, ValueError):
        raise StudioError(
            "invalid_ocr_response",
            "OCR returned incomplete text or invalid layout coordinates",
            502,
        )


class BodhanOCRProvider:
    def __init__(self, key, attempts=4, transport=None):
        self.key, self.attempts, self.transport = key, attempts, transport

    async def extract(self, png):
        if not self.key:
            raise StudioError(
                "missing_ocr_api_key",
                "Set BODHAN_OCR_API_KEY to a key authorized for indic-ocr",
                503,
            )
        payload = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(png).decode("ascii")
                            },
                        }
                    ],
                }
            ],
            "table_format": "html",
            "max_tokens": 4096,
        }
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
            for attempt in range(self.attempts):
                response = None
                try:
                    with timed_span("ocr_http", attempt=attempt + 1) as timing:
                        response = await client.post(
                            "https://api.bodhan.ai/v1/chat/completions",
                            headers={"Authorization": f"Bearer {self.key}"},
                            json=payload,
                        )
                        timing["status_code"] = response.status_code
                except httpx.TransportError:
                    pass
                if response is not None:
                    if response.status_code in {401, 403}:
                        raise StudioError(
                            "ocr_authentication_failed",
                            "Bodhan key is invalid or not authorized for indic-ocr",
                            503,
                        )
                    if response.is_success:
                        try:
                            return validate_response(response.json())
                        except ValueError:
                            raise StudioError(
                                "invalid_ocr_response", "OCR returned invalid JSON", 502
                            )
                    if response.status_code != 429 and response.status_code < 500:
                        raise StudioError(
                            "ocr_request_rejected",
                            f"Bodhan OCR rejected the request ({response.status_code})",
                            502,
                        )
                if attempt + 1 == self.attempts:
                    raise StudioError(
                        "ocr_unavailable", "Bodhan OCR retry budget exhausted", 502
                    )
                try:
                    delay = (
                        float(response.headers.get("retry-after", 2**attempt))
                        if response is not None
                        else 2**attempt
                    )
                except ValueError:
                    delay = 2**attempt
                with timed_span(
                    "ocr_retry_wait",
                    status_code=response.status_code if response is not None else None,
                ):
                    await asyncio.sleep(max(0, min(delay, 60)))
