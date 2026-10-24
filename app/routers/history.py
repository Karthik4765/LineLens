"""Read-only access to the current user's explanation history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.errors import APIError
from app.models import Explanation, Submission, User
from app.schemas import (
    ExplainResponse,
    HistoryItem,
    HistoryList,
    LineExplanationOut,
)

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=HistoryList, summary="List your past explanations")
def list_history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
) -> HistoryList:
    """Return the current user's submissions, newest first."""

    rows = db.execute(
        select(Submission, Explanation)
        .join(Explanation, Explanation.code_hash == Submission.code_hash)
        .where(Submission.user_id == user.id)
        .order_by(Submission.created_at.desc(), Submission.id.desc())
        .limit(limit)
    ).all()
    return HistoryList(
        items=[
            HistoryItem(
                id=submission.id,
                summary=explanation.summary,
                line_count=len(explanation.lines),
                provider=explanation.provider,
                created_at=submission.created_at,
            )
            for submission, explanation in rows
        ]
    )


@router.get(
    "/{submission_id}",
    response_model=ExplainResponse,
    summary="Read one past explanation",
)
def get_history_item(
    submission_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExplainResponse:
    """Return one submission owned by the current user, or a 404.

    Ownership is part of the query so another user's id is indistinguishable
    from an id that does not exist.
    """

    submission = db.scalar(
        select(Submission).where(
            Submission.id == submission_id,
            Submission.user_id == user.id,
        )
    )
    if submission is None:
        raise APIError(404, "not_found", "Explanation not found.")

    explanation = db.scalar(
        select(Explanation).where(Explanation.code_hash == submission.code_hash)
    )
    if explanation is None:
        raise APIError(404, "not_found", "Explanation not found.")

    source_lines = submission.code.splitlines()
    lines = [
        LineExplanationOut(
            line=item["line"],
            code=source_lines[item["line"] - 1],
            explanation=item["explanation"],
        )
        for item in explanation.lines
    ]
    return ExplainResponse(
        id=submission.id,
        code=submission.code,
        summary=explanation.summary,
        lines=lines,
        analysis=explanation.analysis,
        provider=explanation.provider,
        cached=False,
        created_at=submission.created_at,
    )
