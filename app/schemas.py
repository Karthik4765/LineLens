"""Pydantic request and response schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes.")
        return value


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ErrorContext(BaseModel):
    """The last failure reported by the runner, forwarded to the model as context."""

    type: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    hint: str | None = Field(default=None, max_length=500)
    phase: Literal["syntax", "runtime"] = "runtime"


class ExplainRequest(BaseModel):
    code: str
    error_context: ErrorContext | None = None


class RunRequest(BaseModel):
    code: str


class RunErrorOut(BaseModel):
    type: str
    message: str
    line: int | None = None
    column: int | None = None
    hint: str
    phase: str


class RunResponse(BaseModel):
    ok: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool
    error: RunErrorOut | None = None


class LineExplanationOut(BaseModel):
    line: int
    code: str
    explanation: str


class ExplainResponse(BaseModel):
    id: int
    code: str
    summary: str
    lines: list[LineExplanationOut]
    analysis: dict[str, Any]
    provider: str
    cached: bool
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class HistoryItem(BaseModel):
    id: int
    summary: str
    line_count: int
    provider: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class HistoryList(BaseModel):
    items: list[HistoryItem]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str
