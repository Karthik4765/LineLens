"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import unauthorized
from app.models import User
from app.security import TokenError, decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve and return the authenticated user from the bearer token."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()

    try:
        user_id = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise unauthorized(str(exc)) from exc

    user = db.get(User, user_id)
    if user is None:
        raise unauthorized("Your account no longer exists.")
    return user
