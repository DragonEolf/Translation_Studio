"""Bodhan transliteration uses the existing Bodhan bearer key."""
import asyncio
import time
import httpx
from app.config import StudioError
from app.providers.bodhan import validate_target


class BodhanTransliterationProvider:
    def __init__(self, key, attempts=4, transport=None):
        self.key, self.attempts, self.transport = key, attempts, transport
        self.next_request = 0.

    async def transliterate(self, text, language):
        validate_target(language, 'native')
        if language == 'en':
            raise StudioError('unsupported_language', 'Choose an Indic target for transliteration', 422)
        if not self.key:
            raise StudioError('missing_api_key', 'Set BODHAN_API_KEY before transliterating', 503)
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            for attempt in range(self.attempts):
                await asyncio.sleep(max(0, self.next_request-time.monotonic()))
                response = None
                try:
                    response = await client.post('https://api.bodhan.ai/transliterate',
                        headers={'Authorization':f'Bearer {self.key}'},
                        json={'text':text,'language':language,'script':'native'})
                except httpx.TransportError:
                    pass
                if response is not None:
                    try:
                        allowance=float(response.headers.get('x-ratelimit-limit-requests', 0))
                        if allowance > 0:
                            self.next_request=time.monotonic()+60/allowance
                    except ValueError:
                        pass
                    if response.status_code in {401,403}:
                        raise StudioError('transliteration_authentication_failed', 'The Bodhan key was rejected for transliteration', 503)
                    if response.is_success:
                        try:
                            body=response.json()
                            value=body['transliteration']
                            if body.get('truncated') or not isinstance(value,str) or not value.strip():
                                raise ValueError()
                            return {'transliteration':value.strip(),'usage':body.get('usage',{})}
                        except (ValueError,KeyError,TypeError):
                            raise StudioError('invalid_transliteration', 'Bodhan returned incomplete transliteration', 502)
                    if response.status_code != 429 and response.status_code < 500:
                        raise StudioError('transliteration_rejected', f'Bodhan rejected transliteration ({response.status_code})', 502)
                if attempt+1 == self.attempts:
                    raise StudioError('transliteration_unavailable','Bodhan transliteration retry budget exhausted',502)
                try:
                    delay=float(response.headers.get('retry-after',2**attempt)) if response is not None else 2**attempt
                except ValueError:
                    delay=2**attempt
                await asyncio.sleep(max(0,min(delay,60)))
