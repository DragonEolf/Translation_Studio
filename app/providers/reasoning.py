from typing import Protocol
from app.config import StudioError


class ReasoningProvider(Protocol):
    async def solve(self, issue: dict, context: dict) -> dict: ...
    async def revise(self, instruction: str, context: dict) -> str: ...


class DisabledReasoningProvider:
    async def solve(self, issue, context):
        return {"status": "manual_review_required", "issue": issue}

    async def revise(self, instruction, context):
        raise StudioError("reasoning_provider_not_configured", "Configure a reasoning provider to use reviewer instructions", 503)
