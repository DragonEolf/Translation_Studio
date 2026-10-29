"""Isolated settings for historical repair benchmarks, unused by the app."""
from typing import Literal
from pydantic import Field
from app.config import Settings


class LegacyRepairSettings(Settings):
    openrouter_api_key: str = ""
    studio_reasoning_model: str = "deepseek/deepseek-v4.1-flash-1017"
    studio_reasoning_timeout_seconds: float = Field(default=60, ge=1, le=180)
    studio_vision_review_enabled: bool = False
    studio_vision_provider: Literal['openrouter', 'deepseek'] = 'openrouter'
    studio_vision_model: str = ''
    deepseek_api_key: str = ''
    studio_vision_max_regions: int = Field(default=3, ge=1, le=10)
    studio_vision_timeout_seconds: float = Field(default=20, ge=1, le=60)
