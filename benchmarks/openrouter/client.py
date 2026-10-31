"""Small OpenRouter client with bounded retries and persisted usage metadata."""

import asyncio
import time

import httpx


class OpenRouter:
    def __init__(self, key, *, transport=None, attempts=3, deadline=180):
        if not key:
            raise ValueError("Set OPENROUTER_API_KEY in .env or the environment")
        self.key, self.transport, self.attempts = key, transport, attempts
        self.deadline = deadline

    def complete(self, model, system, content, schema, max_tokens, *, one_shot=False):
        async def bounded():
            async with asyncio.timeout(self.deadline):
                return await self._complete(
                    model, system, content, schema, max_tokens, one_shot=one_shot
                )

        try:
            return asyncio.run(bounded())
        except TimeoutError:
            raise RuntimeError(
                f"OpenRouter exceeded the {self.deadline}s overall deadline"
            ) from None

    async def _complete(
        self, model, system, content, schema, max_tokens, *, one_shot=False
    ):
        started = time.monotonic()
        attempts = 1 if one_shot else self.attempts
        request = dict(
            model=model,
            temperature=0.2,
            max_tokens=max_tokens,
            messages=[
                dict(role="system", content=system),
                dict(role="user", content=content),
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "benchmark_result",
                    "strict": True,
                    "schema": schema,
                },
            },
            provider={"require_parameters": True, "allow_fallbacks": not one_shot},
        )
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            for attempt in range(attempts):
                try:
                    response = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self.key}"},
                        json=request,
                    )
                except httpx.TransportError:
                    if attempt + 1 == attempts:
                        raise RuntimeError("OpenRouter connection failed") from None
                    await asyncio.sleep(2**attempt)
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 < attempts:
                        await asyncio.sleep(2**attempt)
                        continue
                if not response.is_success:
                    raise RuntimeError(
                        f"OpenRouter rejected request (HTTP {response.status_code})"
                    )
                body = response.json()
                if body.get("error"):
                    raise RuntimeError("OpenRouter returned a provider error")
                try:
                    choice = body["choices"][0]
                    answer = choice["message"]["content"]
                    if choice.get("finish_reason") != "stop" or not isinstance(
                        answer, str
                    ):
                        raise ValueError()
                except (KeyError, IndexError, TypeError, ValueError):
                    raise RuntimeError(
                        "OpenRouter returned an incomplete response"
                    ) from None
                return answer, {
                    "id": body.get("id"),
                    "model": body.get("model", model),
                    "provider": body.get("provider"),
                    "usage": body.get("usage", {}),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
