"""Cooperative cancellation across HTTP awaits and PDF worker threads."""

import asyncio
from contextlib import suppress
from threading import Event


class JobCancelled(Exception):
    pass


class CancellationToken:
    def __init__(self):
        self.event = Event()

    def cancel(self):
        self.event.set()

    def check(self):
        if self.event.is_set():
            raise JobCancelled()

    async def wait(self, awaitable):
        """Interrupt pending API calls/retry sleeps without killing PDF threads."""
        task = asyncio.ensure_future(awaitable)
        try:
            while not task.done():
                self.check()
                await asyncio.wait({task}, timeout=0.1)
            self.check()
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
