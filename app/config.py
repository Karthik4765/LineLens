"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_JWT_SECRET = "dev-only-change-this-secret"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


class Settings(BaseModel):
    """Validated runtime settings.

    SQLite is used when DATABASE_URL is unset, which keeps local setup and the
    test suite free of external services.
    """

    model_config = ConfigDict(frozen=True)

    app_name: str = "LineLens"
    database_url: str = Field(
        default_factory=lambda: _env("DATABASE_URL") or "sqlite:///./linelens.db"
    )
    jwt_secret: str = Field(default_factory=lambda: _env("JWT_SECRET", DEFAULT_JWT_SECRET))
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(
        default_factory=lambda: _env_int("JWT_EXPIRE_MINUTES", 1440)
    )

    llm_provider: str = Field(default_factory=lambda: _env("LLM_PROVIDER", "gemini").lower())
    gemini_api_key: str = Field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = Field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.5-flash"))
    groq_api_key: str = Field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_model: str = Field(
        default_factory=lambda: _env("GROQ_MODEL", "llama-3.3-70b-versatile")
    )

    rate_limit_requests: int = Field(
        default_factory=lambda: _env_int("RATE_LIMIT_REQUESTS", 10), gt=0
    )
    rate_limit_window_seconds: int = Field(
        default_factory=lambda: _env_int("RATE_LIMIT_WINDOW_SECONDS", 60), gt=0
    )
    daily_ai_limit: int = Field(
        default_factory=lambda: _env_int("DAILY_AI_LIMIT", 50), gt=0
    )
    max_code_chars: int = Field(
        default_factory=lambda: _env_int("MAX_CODE_CHARS", 5000), gt=0
    )
    max_code_lines: int = Field(
        default_factory=lambda: _env_int("MAX_CODE_LINES", 300), gt=0
    )
    run_rate_limit_requests: int = Field(
        default_factory=lambda: _env_int("RUN_RATE_LIMIT_REQUESTS", 20), gt=0
    )
    run_rate_limit_window_seconds: int = Field(
        default_factory=lambda: _env_int("RUN_RATE_LIMIT_WINDOW_SECONDS", 60), gt=0
    )
    run_timeout_seconds: int = Field(
        default_factory=lambda: _env_int("RUN_TIMEOUT_SECONDS", 5), gt=0
    )
    trace_timeout_seconds: float = Field(
        default_factory=lambda: _env_float("TRACE_TIMEOUT_SECONDS", 3.0), gt=0
    )
    trace_max_events: int = Field(
        default_factory=lambda: _env_int("TRACE_MAX_EVENTS", 500), gt=0
    )
    trace_max_output_chars: int = Field(
        default_factory=lambda: _env_int("TRACE_MAX_OUTPUT_CHARS", 10_000), gt=0
    )
    trace_memory_limit_mb: int = Field(
        default_factory=lambda: _env_int("TRACE_MEMORY_LIMIT_MB", 256), gt=0
    )

    @property
    def uses_default_jwt_secret(self) -> bool:
        return self.jwt_secret == DEFAULT_JWT_SECRET


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings object."""

    return Settings()
