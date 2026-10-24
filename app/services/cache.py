"""Code normalization and content-addressed explanation caching."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Explanation


def normalize_code(code: str) -> str:
    """Unify line endings and remove trailing whitespace from every line."""

    unified = code.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in unified.split("\n"))


def hash_code(normalized_code: str) -> str:
    """Return the SHA-256 cache key for normalized source code."""

    return hashlib.sha256(normalized_code.encode("utf-8")).hexdigest()


def get_cached_explanation(db: Session, code_hash: str) -> Explanation | None:
    """Look up a stored explanation by its content hash."""

    return db.scalar(select(Explanation).where(Explanation.code_hash == code_hash))
