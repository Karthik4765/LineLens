"""Integration tests for request limits and daily AI usage caps."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.llm.fake import FakeProvider
from app.routers import explain as explain_router


def explain(client: TestClient, headers: dict[str, str], code: str):
    return client.post("/api/explain", json={"code": code}, headers=headers)


def test_eleventh_request_in_a_minute_returns_429_with_retry_after(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
):
    code = "print('limited')\n"
    for _ in range(10):
        assert explain(client, auth_headers, code).status_code == 200

    response = explain(client, auth_headers, code)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    retry_after = int(response.headers["retry-after"])
    assert 1 <= retry_after <= 60
    assert response.json()["error"]["retry_after"] == retry_after
    assert len(fake_provider.calls) == 1


def test_daily_cap_blocks_new_ai_calls_but_not_cache_hits(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
    monkeypatch,
):
    limited_settings = explain_router.settings.model_copy(update={"daily_ai_limit": 2})
    monkeypatch.setattr(explain_router, "settings", limited_settings)

    first = explain(client, auth_headers, "print('one')\n")
    second = explain(client, auth_headers, "print('two')\n")
    cached = explain(client, auth_headers, "print('one')\n")
    blocked = explain(client, auth_headers, "print('three')\n")

    assert first.status_code == 200
    assert second.status_code == 200
    assert cached.status_code == 200
    assert cached.json()["cached"] is True
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "daily_limit_reached"
    assert int(blocked.headers["retry-after"]) > 0
    assert len(fake_provider.calls) == 2
