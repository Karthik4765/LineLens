"""Shared API error type and helper functions."""

from __future__ import annotations

from typing import Any


class APIError(Exception):
    """An intentional client-facing error with a stable machine-readable code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
        self.extra = extra


def unauthorized(message: str = "Please log in to continue.") -> APIError:
    return APIError(
        401,
        "unauthorized",
        message,
        headers={"WWW-Authenticate": "Bearer"},
    )
