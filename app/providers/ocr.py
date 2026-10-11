import asyncio
import base64
import math
from typing import Protocol
import httpx
from app.config import StudioError


class OCRProvider(Protocol):
    async def extract(self, png: bytes) -> dict: ...


def validate_response(body):
    try:
        if body["choices"][0]["finish_reason"] != "stop" or body.get("truncated"):
            raise ValueError()
        blocks = body["blocks"]
        if not isinstance(blocks, list):
            raise ValueError()
        for block in blocks:
            if not isinstance(block.get("order",0),int) or not isinstance(block.get("label","Paragraph"),str):
                raise ValueError()
            bbox = block["bbox_xyxy"]
            if len(bbox) != 4 or not all(isinstance(v, (float,int)) and math.isfinite(v) for v in bbox):
                raise ValueError()
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1] or not isinstance(block["text"],str):
                raise ValueError()
            confidence = block.get("conf", 0)
            if not isinstance(confidence,(int,float)) or not 0 <= confidence <= 1:
                raise ValueError()
        return body
    except (KeyError, IndexError, TypeError, ValueError):
        raise StudioError("invalid_ocr_response", "OCR returned incomplete text or invalid layout coordinates", 502)


class BodhanOCRProvider:
    def __init__(self, key, attempts=4, transport=None):
        self.key, self.attempts, self.transport = key, attempts, transport

    async def extract(self, png):
        if not self.key:
            raise StudioError("missing_ocr_api_key", "Set BODHAN_OCR_API_KEY to a key authorized for indic-ocr", 503)
        payload = {"model":"indic-ocr", "messages":[{"role":"user","content":[{"type":"image_url","image_url":{"url":"data:image/png;base64,"+base64.b64encode(png).decode("ascii")}}]}], "table_format":"html", "max_tokens":4096}
        async with httpx.AsyncClient(timeout=120,transport=self.transport) as client:
            for attempt in range(self.attempts):
                response = None
                try:
                    response = await client.post("https://api.bodhan.ai/v1/chat/completions",headers={"Authorization":f"Bearer {self.key}"},json=payload)
                except httpx.TransportError:
                    pass
                if response is not None:
                    if response.status_code in {401,403}:
                        raise StudioError("ocr_authentication_failed", "Bodhan key is invalid or not authorized for indic-ocr", 503)
                    if response.is_success:
                        try:
                            return validate_response(response.json())
                        except ValueError:
                            raise StudioError("invalid_ocr_response", "OCR returned invalid JSON", 502)
                    if response.status_code != 429 and response.status_code < 500:
                        raise StudioError("ocr_request_rejected", f"Bodhan OCR rejected the request ({response.status_code})",502)
                if attempt+1 == self.attempts:
                    raise StudioError("ocr_unavailable", "Bodhan OCR retry budget exhausted",502)
                try:
                    delay = float(response.headers.get("retry-after",2**attempt)) if response is not None else 2**attempt
                except ValueError:
                    delay = 2**attempt
                await asyncio.sleep(max(0,min(delay,60)))
