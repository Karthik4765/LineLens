"""Code normalization and content-addressed explanation caching."""

from __future__ import annotations

import ast
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Explanation

#: Programs that import these modules can produce a different trace on every
#: run, so their explanations must never be reused from the cache.
NONDETERMINISTIC_MODULES = frozenset({"random", "time", "datetime"})


def normalize_code(code: str) -> str:
    """Unify line endings and remove trailing whitespace from every line."""

    unified = code.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in unified.split("\n"))


def hash_code(normalized_code: str) -> str:
    """Return the SHA-256 cache key for normalized source code."""

    return hashlib.sha256(normalized_code.encode("utf-8")).hexdigest()


def explanation_hash(
    normalized_code: str,
    error_context: dict[str, Any] | None = None,
    stdin: str = "",
) -> str:
    """Return a cache key for the code, optional stdin, and failure context.

    Empty stdin and no error context keep the original code-only key so existing
    cached explanations stay valid. A program can read stdin, so different input
    must never share an explanation.
    """

    payload = normalized_code
    if stdin:
        payload += "\x00stdin\x00" + stdin
    if error_context:
        context = json.dumps(error_context, ensure_ascii=False, sort_keys=True, default=str)
        payload += "\x00error-context\x00" + context
    return hash_code(payload)


def uses_nondeterministic_imports(code: str) -> bool:
    """Return ``True`` when the source imports a module with changing output.

    Only import statements are inspected. This deliberately errs on the side of
    not caching: a false negative would reuse a trace that no longer matches the
    program's real output.
    """

    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in NONDETERMINISTIC_MODULES:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in NONDETERMINISTIC_MODULES:
                return True
    return False


def get_cached_explanation(db: Session, code_hash: str) -> Explanation | None:
    """Look up a stored explanation by its content hash."""

    return db.scalar(select(Explanation).where(Explanation.code_hash == code_hash))
