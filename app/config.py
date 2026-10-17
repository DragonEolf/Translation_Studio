from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    bodhan_api_key: str = ""
    bodhan_ocr_api_key: str = ""
    studio_ocr_dpi: int = Field(default=150,ge=72,le=300)
    openrouter_api_key: str = ""
    studio_data_dir: Path = Path("data")
    studio_font_dir: Path | None = None
    studio_max_upload_mb: int = 25
    studio_max_pages: int = 100
    studio_provider_attempts: int = 4
    studio_translation_batch_size: int = Field(default=30,ge=1,le=100)
    studio_translation_batch_chars: int = Field(default=6000,ge=256,le=24000)
    studio_cache_enabled: bool = False


class StudioError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)
