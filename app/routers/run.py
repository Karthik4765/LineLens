"""Run a Python snippet and return its output plus a learner-friendly diagnosis."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.config import get_settings
from app.deps import get_current_user
from app.errors import APIError
from app.models import User
from app.schemas import RunErrorOut, RunRequest, RunResponse
from app.services.rate_limiter import SlidingWindowRateLimiter
from app.services.runner import run_code

router = APIRouter(prefix="/api", tags=["run"])
settings = get_settings()


def get_run_rate_limiter(request: Request) -> SlidingWindowRateLimiter:
    """Return the separate limiter used for code execution."""

    return request.app.state.run_rate_limiter


def _validate_run_input(code: str) -> None:
    if not code.strip():
        raise APIError(422, "empty_code", "Paste some Python code to run.")
    if len(code) > settings.max_code_chars:
        raise APIError(
            422,
            "code_too_long",
            f"Code must be at most {settings.max_code_chars} characters.",
        )
    if len(code.splitlines()) > settings.max_code_lines:
        raise APIError(
            422,
            "too_many_lines",
            f"Code must be at most {settings.max_code_lines} lines.",
        )


@router.post("/run", response_model=RunResponse, summary="Run Python code")
def run_snippet(
    payload: RunRequest,
    user: User = Depends(get_current_user),
    limiter: SlidingWindowRateLimiter = Depends(get_run_rate_limiter),
) -> RunResponse:
    """Execute a snippet with a wall-clock timeout and return stdout/stderr.

    Syntax and indentation problems are part of the normal result (``ok`` is
    false), not a validation error, so the UI can show the mistake and the
    explanation prompt can include it.
    """

    rate = limiter.check(user.id)
    if not rate.allowed:
        raise APIError(
            429,
            "rate_limited",
            f"Too many runs. Try again in {rate.retry_after} seconds.",
            headers={"Retry-After": str(rate.retry_after)},
            retry_after=rate.retry_after,
        )

    _validate_run_input(payload.code)
    result = run_code(payload.code, timeout_seconds=settings.run_timeout_seconds)
    error = (
        RunErrorOut(
            type=result.error.type,
            message=result.error.message,
            line=result.error.line,
            column=result.error.column,
            hint=result.error.hint,
            phase=result.error.phase,
        )
        if result.error is not None
        else None
    )
    return RunResponse(
        ok=result.ok,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        timed_out=result.timed_out,
        error=error,
    )
