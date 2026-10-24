"""Password hashing and JWT helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import get_settings


class TokenError(Exception):
    """Raised when a bearer token is missing, malformed, or expired."""


def hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt."""

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored bcrypt hash without raising on bad input."""

    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int) -> str:
    """Create a signed JWT for a user."""

    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int:
    """Return the user id encoded in a valid token, or raise TokenError."""

    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
        subject = payload["sub"]
        return int(subject)
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise TokenError("Invalid or expired token.") from exc
