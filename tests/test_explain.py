"""End-to-end tests for the explain endpoint with a fake AI provider."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.llm.base import ProviderRateLimitError, ProviderServerError
from app.llm.fake import FakeProvider
from app.models import Explanation, UsageLog
from app.services.explainer import Explainer


def explain(client: TestClient, headers: dict[str, str], code: str):
    return client.post("/api/explain", json={"code": code}, headers=headers)


def test_explain_success_returns_lines_and_analysis(
    client: TestClient, auth_headers: dict[str, str], fake_provider: FakeProvider
):
    code = "def greet(name):\n    return f'Hello {name}'\n\nprint(greet('Ada'))\n"

    response = explain(client, auth_headers, code)

    assert response.status_code == 200
    body = response.json()
    assert body["cached"] is False
    assert body["provider"] == "fake"
    assert body["summary"]
    assert body["analysis"]["functions"][0]["name"] == "greet"
    assert [item["line"] for item in body["lines"]] == [1, 2, 4]
    assert body["lines"][0]["code"] == "def greet(name):"
    assert body["lines"][0]["explanation"]
    assert body["created_at"].endswith("Z") or "+00:00" in body["created_at"]
    assert len(fake_provider.calls) == 1


def test_cache_hit_does_not_call_provider_again(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
    db_session: Session,
):
    first = explain(client, auth_headers, "print('hello')\n")
    second = explain(client, auth_headers, "print('hello')   \r\n")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert second.json()["summary"] == first.json()["summary"]
    assert len(fake_provider.calls) == 1
    assert db_session.scalar(select(func.count()).select_from(Explanation)) == 1

    usage = db_session.scalars(select(UsageLog).order_by(UsageLog.id)).all()
    assert len(usage) == 2
    assert [row.cached for row in usage] == [False, True]


def test_syntax_error_returns_422_and_never_calls_provider(
    client: TestClient, auth_headers: dict[str, str], fake_provider: FakeProvider
):
    response = explain(client, auth_headers, "def broken(:\n    pass\n")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "syntax_error"
    assert error["line"] == 1
    assert "Line 1" in error["message"]
    assert error["syntax_message"]
    assert error["error_type"] == "SyntaxError"
    assert error["hint"]
    assert error["phase"] == "syntax"
    assert fake_provider.calls == []


def test_empty_input_is_rejected(
    client: TestClient, auth_headers: dict[str, str], fake_provider: FakeProvider
):
    response = explain(client, auth_headers, "  \n\n ")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_code"
    assert fake_provider.calls == []


def test_oversized_input_is_rejected_before_parsing(
    client: TestClient, auth_headers: dict[str, str], fake_provider: FakeProvider
):
    response = explain(client, auth_headers, "x" * 5001)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "code_too_long"
    assert fake_provider.calls == []


def test_too_many_lines_is_rejected(
    client: TestClient, auth_headers: dict[str, str], fake_provider: FakeProvider
):
    response = explain(client, auth_headers, "pass\n" * 301)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "too_many_lines"
    assert fake_provider.calls == []


def test_non_python_input_returns_syntax_error(
    client: TestClient, auth_headers: dict[str, str], fake_provider: FakeProvider
):
    response = explain(client, auth_headers, "print 'not python 3'\n")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "syntax_error"
    assert fake_provider.calls == []


def test_invalid_json_is_retried_once_then_succeeds(
    client: TestClient,
    auth_headers: dict[str, str],
    set_explainer,
):
    provider = FakeProvider(invalid_attempts=1)
    set_explainer(Explainer([provider]))

    response = explain(client, auth_headers, "print('retry')\n")

    assert response.status_code == 200
    assert len(provider.calls) == 2


def test_two_invalid_json_responses_return_502_without_raw_text(
    client: TestClient,
    auth_headers: dict[str, str],
    set_explainer,
):
    provider = FakeProvider(invalid_attempts=2)
    set_explainer(Explainer([provider]))

    response = explain(client, auth_headers, "print('bad json')\n")

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "invalid_ai_response"
    assert "deliberately not JSON" not in body["error"]["message"]
    assert len(provider.calls) == 2


def test_rate_limited_primary_falls_back_to_backup(
    client: TestClient,
    auth_headers: dict[str, str],
    set_explainer,
):
    primary = FakeProvider(
        name="gemini", fail_with=ProviderRateLimitError("quota exhausted")
    )
    backup = FakeProvider(name="groq")
    set_explainer(Explainer([primary, backup]))

    response = explain(client, auth_headers, "print('fallback')\n")

    assert response.status_code == 200
    assert response.json()["provider"] == "groq"
    assert len(primary.calls) == 1
    assert len(backup.calls) == 1


def test_invalid_primary_json_does_not_fall_back(
    client: TestClient,
    auth_headers: dict[str, str],
    set_explainer,
):
    primary = FakeProvider(name="gemini", invalid_attempts=2)
    backup = FakeProvider(name="groq")
    set_explainer(Explainer([primary, backup]))

    response = explain(client, auth_headers, "print('contract failure')\n")

    assert response.status_code == 502
    assert len(primary.calls) == 2
    assert backup.calls == []


def test_all_providers_unavailable_returns_503(
    client: TestClient,
    auth_headers: dict[str, str],
    set_explainer,
):
    primary = FakeProvider(name="gemini", fail_with=ProviderRateLimitError("limited"))
    backup = FakeProvider(name="groq", fail_with=ProviderServerError("down"))
    set_explainer(Explainer([primary, backup]))

    response = explain(client, auth_headers, "print('down')\n")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"


def test_explain_requires_authentication(client: TestClient):
    response = client.post("/api/explain", json={"code": "print('hi')\n"})

    assert response.status_code == 401


def test_error_context_is_forwarded_and_cached_separately(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
):
    code = "print(1 / 0)\n"
    error_context = {
        "type": "ZeroDivisionError",
        "message": "division by zero",
        "line": 1,
        "hint": "Guard the denominator before dividing.",
        "phase": "runtime",
    }

    plain = explain(client, auth_headers, code)
    with_error = client.post(
        "/api/explain",
        json={"code": code, "error_context": error_context},
        headers=auth_headers,
    )
    repeated = client.post(
        "/api/explain",
        json={"code": code, "error_context": error_context},
        headers=auth_headers,
    )

    assert plain.status_code == 200
    assert with_error.status_code == 200
    assert repeated.status_code == 200
    assert plain.json()["cached"] is False
    assert with_error.json()["cached"] is False
    assert repeated.json()["cached"] is True
    assert "ZeroDivisionError" in with_error.json()["summary"]
    assert "ZeroDivisionError" not in plain.json()["summary"]
    assert len(fake_provider.calls) == 2
    assert "THE PROGRAM FAILED WHEN RUN" in fake_provider.calls[1]

