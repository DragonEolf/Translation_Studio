"""Sanitized per-attempt provider diagnostics; never stores bodies or credentials."""
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx


def status_category(status):
    if status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "authentication"
    if status >= 500:
        return "provider_server_error"
    if status >= 400:
        return "request_rejected"
    return "success"


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, path, service, *, transport=None):
        self.path, self.service, self.transport = Path(path), service, transport

    async def handle_async_request(self, request):
        started = time.monotonic()
        event = dict(at=datetime.now(timezone.utc).isoformat(), service=self.service)
        try:
            # Fully buffer within the transport lifetime, including read timeouts.
            # Each attempt uses a fresh pool; no client connection survives a call.
            if self.transport is None:
                async with httpx.AsyncHTTPTransport() as transport:
                    response = await transport.handle_async_request(request)
                    await response.aread()
            else:
                response = await self.transport.handle_async_request(request)
                await response.aread()
            event.update(http_status=response.status_code, category=status_category(response.status_code))
            # Persist only numeric limit headers, never arbitrary provider strings.
            for name in ("retry-after", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests"):
                try:
                    value = float(response.headers[name])
                    if value >= 0 and value < float("inf"):
                        event[name] = value
                except (KeyError, ValueError):
                    pass
            if response.is_success:
                try:
                    body = response.json()
                    if isinstance(body, dict) and body.get("error"):
                        error = body["error"]
                        code = error.get("code") if isinstance(error, dict) else None
                        event["category"] = status_category(code) if type(code) is int and code >= 400 else "provider_error_response"
                        if type(code) is int:
                            event["response_error_code"] = code
                except ValueError:
                    event["category"] = "invalid_json_response"
            return response
        except httpx.TimeoutException as exc:
            event.update(category="timeout", exception_type=type(exc).__name__)
            raise
        except httpx.TransportError as exc:
            event.update(category="connection_error", exception_type=type(exc).__name__)
            raise
        except asyncio.CancelledError:
            event.update(category="cancelled")
            raise
        finally:
            event["elapsed_seconds"] = round(time.monotonic()-started, 3)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as stream:
                stream.write(json.dumps(event)+"\n")


def request_summary(directory):
    services = {}
    for path in Path(directory).glob("*-requests.jsonl"):
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        services[path.stem.removesuffix("-requests")] = dict(
            attempts=len(events), categories=dict(Counter(e["category"] for e in events)),
            http_statuses=dict(Counter(str(e["http_status"]) for e in events if "http_status" in e)))
    return services


def failure_details(exc, directory, stage):
    code = getattr(exc, "code", None)
    service = "openrouter" if stage == "comparison" else "bodhan"
    events = []
    paths = [Path(directory) / f"{service}-requests.jsonl"]
    if stage == "translation":
        paths.append(Path(directory) / "ocr-requests.jsonl")
    for path in paths:
        if path.exists():
            events.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    events.sort(key=lambda e: e["at"])
    category = "internal_error"
    if isinstance(exc, (ValueError,)):
        category = "evaluation_validation" if stage == "comparison" else "input_validation"
        if events and events[-1]["category"] == "invalid_json_response":
            category = "invalid_response"
    if code in {"invalid_provider_response", "invalid_ocr_response", "invalid_translation_batch", "protected_value_changed", "translation_truncated"}:
        category = "invalid_response"
    elif code in {"invalid_api_key", "missing_api_key", "missing_ocr_api_key", "ocr_authentication_failed"}:
        category = "authentication"
    elif code in {"provider_unavailable", "ocr_unavailable", "provider_request_rejected", "ocr_request_rejected"}:
        category = events[-1]["category"] if events else "provider_failure_unclassified"
    elif isinstance(exc, RuntimeError):
        if "overall deadline" in str(exc) or "job timed out" in str(exc):
            category = "deadline_exceeded"
        elif events and events[-1]["category"] != "success":
            category = events[-1]["category"]
        elif "incomplete response" in str(exc) or "provider error" in str(exc):
            category = "invalid_response"
    result = dict(error_category=category, error_type=type(exc).__name__)
    if code:
        result["error_code"] = code
    if events:
        result["last_request"] = events[-1]
    return result
