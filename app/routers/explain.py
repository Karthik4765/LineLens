"""The core line-by-line explanation endpoint."""

from __future__ import annotations

import ast
from datetime import datetime, time, timedelta, timezone
from math import ceil

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.errors import APIError
from app.llm.base import InvalidLLMResponse, LLMExplanation, ProviderUnavailableError
from app.models import Explanation, Submission, UsageLog, User
from app.schemas import ExplainRequest, ExplainResponse, LineExplanationOut
from app.services.analyzer import analyze_code
from app.services.cache import get_cached_explanation, hash_code, normalize_code
from app.services.explainer import Explainer, get_explainer
from app.services.rate_limiter import SlidingWindowRateLimiter, get_rate_limiter

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

    try:
        ast.parse(code, filename="<linelens>")
    except SyntaxError as exc:
        line = exc.lineno or 1
        message = f"Line {line}: {exc.msg}"
        raise APIError(
            422,
            "syntax_error",
            message,
            line=line,
            syntax_message=exc.msg,
        ) from exc
    except ValueError as exc:
        raise APIError(
            422,
            "syntax_error",
            "Line 1: The code contains invalid characters.",
            line=1,
            syntax_message=str(exc),
        ) from exc


def _response(
    submission: Submission,
    code: str,
    summary: str,
    lines: list[dict[str, object]],
    analysis: dict[str, object],
    provider: str,
    cached: bool,
) -> ExplainResponse:
    source_lines = code.splitlines()
    output_lines = [
        LineExplanationOut(
            line=int(item["line"]),
            code=source_lines[int(item["line"]) - 1],
            explanation=str(item["explanation"]),
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
    code_hash = hash_code(normalized)

    cached_explanation = get_cached_explanation(db, code_hash)
    if cached_explanation is not None:
        explanation = LLMExplanation.model_validate(
            {"summary": cached_explanation.summary, "lines": cached_explanation.lines}
        )
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
            explanation.summary,
            [line.model_dump() for line in explanation.lines],
            cached_explanation.analysis,
            cached_explanation.provider,
            cached=True,
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
    try:
        result = explainer.explain(normalized, analysis)
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
    db.add(
        Explanation(
            code_hash=code_hash,
            summary=result.explanation.summary,
            lines=lines,
            analysis=analysis,
            provider=result.provider,
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
    )
