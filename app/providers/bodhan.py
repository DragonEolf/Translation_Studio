"""Bodhan translation client, supported targets, and bounded transport retries."""

import asyncio
import time
from typing import Protocol

import httpx

from app.config import StudioError

LANGUAGES = set(
    "as bn brx doi en gu hi kn kok ks ks-Deva mai ml mni mni-Beng mr ne or pa sa sat sd sd-Arab ta te ur".split()
)
LANGUAGE_NAMES = {
    "as": "Assamese",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "en": "English",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "kok": "Konkani",
    "ks": "Kashmiri (Arabic)",
    "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri (Meitei)",
    "mni-Beng": "Manipuri (Bengali)",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi (Devanagari)",
    "sd-Arab": "Sindhi (Arabic)",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}


def validate_target(language, script):
    if language not in LANGUAGES:
        raise StudioError("unsupported_language", "Unsupported Bodhan target language")
    if (
        script not in {"native", "roman", "codemix"}
        or (language == "en" and script != "native")
        or (script == "codemix" and language in {"ks-Deva", "sd-Arab"})
    ):
        raise StudioError(
            "unsupported_script_for_language", "Unsupported language/script combination"
        )


class TranslationProvider(Protocol):
    async def translate(self, text: str, language: str, script: str) -> dict: ...


class BodhanProvider:
    def __init__(self, key, attempts=4, transport=None):
        self.key, self.attempts, self.transport = key, attempts, transport
        self.next_request = 0.0

    async def translate(self, text, language, script):
        validate_target(language, script)
        if not self.key:
            raise StudioError(
                "missing_api_key", "Set BODHAN_API_KEY before translating", 503
            )
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            for attempt in range(self.attempts):
                await asyncio.sleep(max(0, self.next_request - time.monotonic()))
                try:
                    response = await client.post(
                        "https://api.bodhan.ai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self.key}"},
                        json={
                            "model": "indic-translate",
                            "messages": [{"role": "user", "content": text}],
                            "target_language_code": language,
                            "target_script": script,
                        },
                    )
                except httpx.TransportError:
                    if attempt + 1 == self.attempts:
                        raise StudioError(
                            "provider_unavailable", "Bodhan request failed", 502
                        )
                    await asyncio.sleep(min(2**attempt, 8))
                    continue
                try:
                    allowance = float(
                        response.headers.get("x-ratelimit-limit-requests", "0")
                    )
                    if allowance > 0:
                        self.next_request = time.monotonic() + 60 / allowance
                except ValueError:
                    pass
                if response.status_code == 401:
                    raise StudioError(
                        "invalid_api_key", "Bodhan authentication failed", 503
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 == self.attempts:
                        raise StudioError(
                            "provider_unavailable", "Bodhan retry budget exhausted", 502
                        )
                    try:
                        delay = min(
                            float(response.headers.get("retry-after", 2**attempt)), 60
                        )
                    except ValueError:
                        delay = 2**attempt
                    await asyncio.sleep(max(0, delay))
                    continue
                if response.is_error:
                    raise StudioError(
                        "provider_request_rejected",
                        f"Bodhan rejected the request ({response.status_code})",
                        502,
                    )
                try:
                    body = response.json()
                    choice = body["choices"][0]
                    translation = choice["message"]["content"]
                    if not isinstance(translation, str) or not translation.strip():
                        raise ValueError()
                    if choice.get("finish_reason") not in {"stop", "length"}:
                        raise ValueError()
                except (ValueError, AttributeError, KeyError, IndexError, TypeError):
                    raise StudioError(
                        "invalid_provider_response",
                        "Bodhan returned invalid translation data",
                        502,
                    )
                return {
                    "translation": translation,
                    "target_language": language,
                    "script": script,
                    "truncated": bool(body.get("truncated"))
                    or choice.get("finish_reason") == "length",
                    "finish_reason": choice.get("finish_reason"),
                    "usage": body.get("usage", {}),
                }
