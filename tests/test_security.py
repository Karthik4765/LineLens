"""Security checks for child environments and production configuration."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import DEFAULT_JWT_SECRET, Settings
from app.services.tracer import trace_code

LEAKED_SECRET = "test-jwt-secret-that-must-not-leak-4f8a"
LEAKED_KEY = "test-api-key-that-must-not-leak-9c2d"
ENV_PROBE = "import os\nprint(dict(os.environ))\n"


def _assert_no_secrets(text: str) -> None:
    assert LEAKED_SECRET not in text
    assert LEAKED_KEY not in text
    assert "JWT_SECRET" not in text
    assert "GEMINI_API_KEY" not in text
    assert "GROQ_API_KEY" not in text


def test_run_does_not_leak_server_secrets(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("JWT_SECRET", LEAKED_SECRET)
    monkeypatch.setenv("GEMINI_API_KEY", LEAKED_KEY)
    monkeypatch.setenv("GROQ_API_KEY", LEAKED_KEY)

    response = client.post("/api/run", json={"code": ENV_PROBE}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    _assert_no_secrets(body["stdout"])
    _assert_no_secrets(body["stderr"])


def test_tracer_does_not_leak_server_secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET", LEAKED_SECRET)
    monkeypatch.setenv("GEMINI_API_KEY", LEAKED_KEY)
    monkeypatch.setenv("GROQ_API_KEY", LEAKED_KEY)

    result = trace_code(ENV_PROBE)

    # os is not on the allowlist, so the trace falls back instead of running it.
    assert result.status == "unavailable"
    assert result.note is not None
    assert "not allowed in LineLens" in result.note
    _assert_no_secrets(result.final_output)
    _assert_no_secrets(result.note)


def test_tracer_blocks_open_and_non_allowlisted_imports():
    opened = trace_code("handle = open('secrets.txt')\n")
    assert opened.status == "ok"
    assert opened.error is not None
    assert opened.error.type == "NameError"
    assert "open" in opened.error.message

    imported = trace_code("import socket\n")
    assert imported.status == "unavailable"
    assert imported.note is not None
    assert "not allowed in LineLens" in imported.note


def test_production_refuses_the_default_jwt_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("JWT_SECRET", DEFAULT_JWT_SECRET)

    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings()


def test_production_accepts_a_real_jwt_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("JWT_SECRET", LEAKED_SECRET)

    settings = Settings()

    assert settings.environment == "production"
    assert settings.uses_default_jwt_secret is False
