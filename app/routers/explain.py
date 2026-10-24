"""The core line-by-line explanation endpoint."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, time, timedelta, timezone
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.errors import APIError
from app.llm.base import InvalidLLMResponse, LLMExplanation, ProviderUnavailableError
from app.models import Explanation, Submission, UsageLog, User
from app.schemas import (
    ExplainRequest,
    ExplainResponse,
    LineExplanationOut,
    TraceErrorOut,
    TraceStepOut,
)
from app.services.analyzer import analyze_code
from app.services.cache import (
    explanation_hash,
    get_cached_explanation,
    hash_code,
    normalize_code,
    uses_nondeterministic_imports,
)
from app.services.explainer import Explainer, get_explainer
from app.services.rate_limiter import SlidingWindowRateLimiter, get_rate_limiter
from app.services.runner import check_syntax
from app.services.tracer import TraceResult, trace_code

router = APIRouter(prefix="/api", tags=["explain"])
settings = get_settings()


def _real_ai_calls_today(db: Session, user_id: int) -> int:
    """Count non-cached explanation calls since midnight UTC."""

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count = db.scalar(
        select(func.count(UsageLog.id)).where(
            UsageLog.user_id == user_id,
            UsageLog.cached.is_(False),
            UsageLog.created_at >= start,
        )
    )
    return int(count or 0)


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    midnight = datetime.combine(tomorrow, time.min, tzinfo=timezone.utc)
    return max(1, ceil((midnight - now).total_seconds()))


def _validate_code(code: str) -> None:
    """Reject empty, oversized, and syntactically invalid Python before any AI call."""

    if not code.strip():
        raise APIError(422, "empty_code", "Paste some Python code to explain.")
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

    syntax_error = check_syntax(code)
    if syntax_error is not None:
        line = syntax_error.line or 1
        raise APIError(
            422,
            "syntax_error",
            f"Line {line}: {syntax_error.message}",
            line=line,
            column=syntax_error.column,
            syntax_message=syntax_error.message,
            error_type=syntax_error.type,
            hint=syntax_error.hint,
            phase="syntax",
        )


def _trace_fields_from_result(trace: TraceResult) -> dict[str, Any]:
    """Turn a fresh trace into response fields."""

    return {
        "trace_status": trace.status,
        "trace_note": trace.note,
        "final_output": trace.final_output,
        "steps": [asdict(step) for step in trace.steps] if trace.available else [],
        "trace_error": asdict(trace.error) if trace.error is not None else None,
    }


def _trace_fields_from_explanation(explanation: Explanation) -> dict[str, Any]:
    """Read trace fields from a stored explanation, tolerating old rows."""

    stored = explanation.trace or {}
    steps = stored.get("steps") if isinstance(stored, dict) else None
    error = stored.get("error") if isinstance(stored, dict) else None
    return {
        "trace_status": explanation.trace_status or "unavailable",
        "trace_note": explanation.trace_note,
        "final_output": explanation.final_output or "",
        "steps": steps if isinstance(steps, list) else [],
        "trace_error": error if isinstance(error, dict) else None,
    }


def _response(
    submission: Submission,
    code: str,
    summary: str,
    lines: list[dict[str, object]],
    analysis: dict[str, object],
    provider: str,
    cached: bool,
    *,
    trace_status: str = "unavailable",
    trace_note: str | None = None,
    final_output: str = "",
    steps: list[dict[str, Any]] | None = None,
    trace_error: dict[str, Any] | None = None,
) -> ExplainResponse:
    source_lines = code.splitlines()
    output_lines = [
        LineExplanationOut(
            line=int(item["line"]),
            code=source_lines[int(item["line"]) - 1],
            explanation=str(item["explanation"]),
            run_count=int(item.get("run_count", 0) or 0),
            runs=[int(value) for value in (item.get("runs") or [])][:5],
        )
        for item in lines
    ]
    return ExplainResponse(
        id=submission.id,
        code=code,
        summary=summary,
        lines=output_lines,
        analysis=analysis,
        provider=provider,
        cached=cached,
        created_at=submission.created_at,
        trace_status="ok" if trace_status == "ok" else "unavailable",
        trace_note=trace_note,
        final_output=final_output,
        steps=[TraceStepOut.model_validate(step) for step in (steps or [])],
        trace_error=TraceErrorOut.model_validate(trace_error) if trace_error else None,
    )


@router.post("/explain", response_model=ExplainResponse, summary="Explain Python code")
def explain_code(
    payload: ExplainRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    explainer: Explainer = Depends(get_explainer),
    limiter: SlidingWindowRateLimiter = Depends(get_rate_limiter),
) -> ExplainResponse:
    """Validate, cache-check, analyze, explain, persist, and return one submission."""

    rate = limiter.check(user.id)
    if not rate.allowed:
        raise APIError(
            429,
            "rate_limited",
            f"Too many requests. Try again in {rate.retry_after} seconds.",
            headers={"Retry-After": str(rate.retry_after)},
            retry_after=rate.retry_after,
        )

    _validate_code(payload.code)
    normalized = normalize_code(payload.code)
    stdin = payload.stdin or ""
    error_context = (
        payload.error_context.model_dump(exclude_none=True)
        if payload.error_context is not None
        else None
    )
    base_hash = explanation_hash(normalized, error_context, stdin)
    cacheable = not uses_nondeterministic_imports(normalized)
    code_hash = base_hash
    if not cacheable:
        # Random/time-based programs get a fresh storage key. History stays
        # accurate, and a later request can never reuse an old trace.
        code_hash = hash_code(f"{base_hash}\x00nonce\x00{uuid.uuid4().hex}")

    cached_explanation = get_cached_explanation(db, code_hash) if cacheable else None
    if cached_explanation is not None:
        submission = Submission(user_id=user.id, code=payload.code, code_hash=code_hash)
        db.add(submission)
        db.add(
            UsageLog(
                user_id=user.id,
                endpoint="/api/explain",
                cached=True,
                provider=cached_explanation.provider,
            )
        )
        db.commit()
        db.refresh(submission)
        return _response(
            submission,
            payload.code,
            cached_explanation.summary,
            cached_explanation.lines,
            cached_explanation.analysis,
            cached_explanation.provider,
            cached=True,
            **_trace_fields_from_explanation(cached_explanation),
        )

    if _real_ai_calls_today(db, user.id) >= settings.daily_ai_limit:
        retry_after = _seconds_until_utc_midnight()
        raise APIError(
            429,
            "daily_limit_reached",
            (
                f"You have used today's {settings.daily_ai_limit} AI explanations. "
                "Cached explanations still work; try again tomorrow."
            ),
            headers={"Retry-After": str(retry_after)},
            retry_after=retry_after,
        )

    analysis = analyze_code(normalized)
    trace = trace_code(
        normalized,
        stdin=stdin,
        timeout_seconds=settings.trace_timeout_seconds,
        max_events=settings.trace_max_events,
        max_output_chars=settings.trace_max_output_chars,
        memory_limit_mb=settings.trace_memory_limit_mb,
    )
    trace_prompt = trace.to_prompt() if trace.available else None
    try:
        result = explainer.explain(normalized, analysis, error_context, trace_prompt)
    except InvalidLLMResponse as exc:
        raise APIError(
            502,
            "invalid_ai_response",
            "The AI returned an invalid explanation. Please try again.",
        ) from exc
    except ProviderUnavailableError as exc:
        raise APIError(
            503,
            "provider_unavailable",
            "The explanation service is unavailable right now. Please try again shortly.",
        ) from exc

    lines = [line.model_dump() for line in result.explanation.lines]
    for item in lines:
        line_number = int(item["line"])
        item["run_count"] = trace.run_counts.get(line_number, 0)
        item["runs"] = trace.run_steps.get(line_number, [])[:5]

    db.add(
        Explanation(
            code_hash=code_hash,
            summary=result.explanation.summary,
            lines=lines,
            analysis=analysis,
            provider=result.provider,
            trace_status=trace.status,
            trace_note=trace.note,
            final_output=trace.final_output,
            trace=trace.to_storage(),
        )
    )
    submission = Submission(user_id=user.id, code=payload.code, code_hash=code_hash)
    db.add(submission)
    db.add(
        UsageLog(
            user_id=user.id,
            endpoint="/api/explain",
            cached=False,
            provider=result.provider,
        )
    )
    db.commit()
    db.refresh(submission)

    return _response(
        submission,
        payload.code,
        result.explanation.summary,
        lines,
        analysis,
        result.provider,
        cached=False,
        **_trace_fields_from_result(trace),
    )
