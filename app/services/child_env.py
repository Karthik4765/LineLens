"""Build the environment for code-execution children.

User code must not be able to read server secrets such as API keys or the JWT
signing key. The child gets almost nothing; Windows needs ``SYSTEMROOT`` for the
Python runtime to start, so that one variable is kept when it is present.
"""

from __future__ import annotations

import os


def minimal_child_env() -> dict[str, str]:
    """Return a small environment with no application secrets."""

    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT") or os.environ.get("SystemRoot")
        return {"SYSTEMROOT": system_root} if system_root else {}
    return {}
