"""API tests for the code-execution endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.routers.run import get_run_rate_limiter
from app.services.rate_limiter import SlidingWindowRateLimiter


def run_code(client: TestClient, headers: dict[str, str], code: str):
    return client.post("/api/run", json={"code": code}, headers=headers)


def test_run_requires_authentication(client: TestClient):
    response = client.post("/api/run", json={"code": "print('hi')\n"})

    assert response.status_code == 401


def test_run_returns_stdout_and_exit_code(
    client: TestClient, auth_headers: dict[str, str]
):
    response = run_code(client, auth_headers, "print('hello')\n")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["stdout"] == "hello\n"
    assert body["stderr"] == ""
    assert body["exit_code"] == 0
    assert body["timed_out"] is False
    assert body["error"] is None


def test_run_returns_syntax_error_as_a_result(
    client: TestClient, auth_headers: dict[str, str]
):
    response = run_code(client, auth_headers, "if True\n    pass\n")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["type"] == "SyntaxError"
    assert body["error"]["line"] == 1
    assert body["error"]["hint"]
    assert "SyntaxError" in body["stderr"]


def test_run_returns_runtime_diagnosis(
    client: TestClient, auth_headers: dict[str, str]
):
    response = run_code(client, auth_headers, "print(1 / 0)\n")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["type"] == "ZeroDivisionError"
    assert body["error"]["line"] == 1
    assert "zero" in body["error"]["hint"].lower()


def test_run_rejects_empty_code(client: TestClient, auth_headers: dict[str, str]):
    response = run_code(client, auth_headers, "  \n")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_code"


def test_run_rejects_oversized_code(client: TestClient, auth_headers: dict[str, str]):
    response = run_code(client, auth_headers, "x" * 5001)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "code_too_long"


def test_run_has_its_own_rate_limit(
    client: TestClient, auth_headers: dict[str, str]
):
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)
    app.dependency_overrides[get_run_rate_limiter] = lambda: limiter

    first = run_code(client, auth_headers, "print(1)\n")
    second = run_code(client, auth_headers, "print(2)\n")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in second.headers
