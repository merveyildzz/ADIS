"""Central application configuration.

All tunables (confidence thresholds, upload limits, DB/LLM connection info)
live here so agents and routes never hardcode them. Values are sourced from
environment variables / a .env file — never hardcoded secrets.
"""
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root (backend/app/config.py -> backend/app -> backend -> repo root),
# so the default DB path is stable regardless of the process's cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DATABASE_URL = f"sqlite:///{_REPO_ROOT / 'data' / 'app.db'}"
_DEFAULT_UPLOADS_DIR = _REPO_ROOT / "data" / "uploads"


class ConfigError(Exception):
    """Raised when configuration is missing/invalid. Caught at startup so the
    app can fail with a clear message instead of a raw traceback."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---
    database_url: str = Field(default=_DEFAULT_DATABASE_URL, alias="DATABASE_URL")

    # --- LLM (optional: every LLM-backed agent has a non-LLM fallback) ---
    llm_api_key: Optional[str] = Field(default=None, alias="LLM_API_KEY")
    llm_provider: str = Field(default="anthropic", alias="LLM_PROVIDER")  # "anthropic" | "gemini"
    llm_model: Optional[str] = Field(default=None, alias="LLM_MODEL")  # overrides the provider's default model

    # --- Upload constraints ---
    max_upload_size_mb: int = Field(default=20, alias="MAX_UPLOAD_SIZE_MB")
    allowed_file_extensions: tuple[str, ...] = (".csv",)
    # Where the original uploaded file is kept so the cleaned data can later
    # be exported with corrections applied — never served directly, only
    # read back internally by the export endpoint.
    uploads_dir: Path = Field(default=_DEFAULT_UPLOADS_DIR, alias="UPLOADS_DIR")

    # --- Confidence score thresholds (0-100) ---
    confidence_high_threshold: int = Field(default=90, alias="CONFIDENCE_HIGH_THRESHOLD")
    confidence_medium_threshold: int = Field(default=60, alias="CONFIDENCE_MEDIUM_THRESHOLD")

    # --- Query pagination ---
    # Hard server-side cap: a caller can ask for fewer rows, never more, so a
    # single request can't pull an unbounded result set into memory.
    max_page_size: int = Field(default=200, alias="MAX_PAGE_SIZE")

    # --- Misc ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # --- CORS (the frontend dev server runs on a different origin) ---
    cors_allowed_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    @field_validator("database_url")
    @classmethod
    def _database_url_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("DATABASE_URL must not be empty")
        return v

    @field_validator("max_upload_size_mb")
    @classmethod
    def _positive_upload_size(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("MAX_UPLOAD_SIZE_MB must be a positive integer")
        return v

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process. Raises ConfigError (never a raw
    validation traceback) if required configuration is missing/invalid."""
    try:
        return Settings()
    except Exception as exc:  # pydantic ValidationError, etc.
        raise ConfigError(
            "Application configuration is invalid or incomplete. "
            "Check your .env file against .env.example. "
            f"Details: {exc}"
        ) from exc
