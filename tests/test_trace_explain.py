"""End-to-end tests for execution tracing through the explain endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.llm.fake import FakeProvider
from app.models import Explanation
from app.routers import explain as explain_router

THREE_LINES = "a, b = 1, 2\nc = a + b\nprint(c)\n"


def explain(
    client: TestClient,
    headers: dict[str, str],
    code: str,
    stdin: str = "",
):
    body = {"code": code}
    if stdin:
        body["stdin"] = stdin
    return client.post("/api/explain", json=body, headers=headers)


def test_three_line_example_returns_real_values_end_to_end(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
):
    response = explain(client, auth_headers, THREE_LINES)

    assert response.status_code == 200
    body = response.json()
    assert body["trace_status"] == "ok"
    assert body["trace_note"] is None
    assert body["final_output"] == "3\n"
    assert body["trace_error"] is None
    assert [step["line"] for step in body["steps"]] == [1, 2, 3]
    assert body["steps"][0]["variables"] == {"a": "1", "b": "2"}
    assert body["steps"][1]["variables"] == {"c": "3"}
    assert body["steps"][2]["stdout"] == "3\n"

    by_line = {item["line"]: item for item in body["lines"]}
    assert by_line[1]["run_count"] == 1
    assert by_line[2]["run_count"] == 1
    assert by_line[3]["run_count"] == 1
    assert by_line[1]["runs"] == [1]
    assert "`a` = `1`" in by_line[1]["explanation"]
    assert "`c` = `3`" in by_line[2]["explanation"]
    assert "`3`" in by_line[3]["explanation"]


def test_prompt_contains_the_worked_example_and_trace_values(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
):
    explain(client, auth_headers, THREE_LINES)

    prompt = fake_provider.calls[0]
    assert "a, b = 1, 2" in prompt
    assert "EXECUTION TRACE" in prompt
    assert '"c":"3"' in prompt
    assert "never invent" in prompt.lower()


def test_stdin_is_part_of_the_cache_key(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
):
    code = "name = input()\nprint(name)\n"

    first = explain(client, auth_headers, code, stdin="Ada\n")
    second = explain(client, auth_headers, code, stdin="Bob\n")
    repeated = explain(client, auth_headers, code, stdin="Ada\n")

    assert first.status_code == 200
    assert second.status_code == 200
    assert repeated.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is False
    assert repeated.json()["cached"] is True
    assert first.json()["final_output"] == "Ada\n"
    assert second.json()["final_output"] == "Bob\n"
    assert repeated.json()["final_output"] == "Ada\n"
    assert len(fake_provider.calls) == 2


def test_nondeterministic_imports_are_never_cached(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
    db_session: Session,
):
    code = "import random\nprint(random.random())\n"

    first = explain(client, auth_headers, code)
    second = explain(client, auth_headers, code)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is False
    assert first.json()["trace_status"] == "ok"
    assert second.json()["trace_status"] == "ok"
    assert len(fake_provider.calls) == 2
    assert db_session.scalar(select(func.count()).select_from(Explanation)) == 2


def test_blocked_import_falls_back_to_a_structure_explanation(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
):
    response = explain(client, auth_headers, "import os\nprint(os.getcwd())\n")

    assert response.status_code == 200
    body = response.json()
    assert body["trace_status"] == "unavailable"
    assert body["trace_note"] is not None
    assert "not allowed in LineLens" in body["trace_note"]
    assert body["steps"] == []
    assert body["lines"]
    assert all(item["run_count"] == 0 for item in body["lines"])
    assert len(fake_provider.calls) == 1


def test_timeout_falls_back_to_a_structure_explanation(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
    monkeypatch,
):
    fast_timeout = explain_router.settings.model_copy(
        update={"trace_timeout_seconds": 0.2, "trace_max_events": 1_000_000}
    )
    monkeypatch.setattr(explain_router, "settings", fast_timeout)

    response = explain(client, auth_headers, "while True:\n    pass\n")

    assert response.status_code == 200
    body = response.json()
    assert body["trace_status"] == "unavailable"
    assert body["trace_note"] is not None
    assert "0.2 seconds" in body["trace_note"]
    assert body["steps"] == []
    assert body["lines"]
    assert len(fake_provider.calls) == 1


def test_history_detail_returns_the_stored_trace(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
):
    created = explain(client, auth_headers, THREE_LINES).json()

    response = client.get(f"/api/history/{created['id']}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["trace_status"] == "ok"
    assert body["final_output"] == "3\n"
    assert [step["line"] for step in body["steps"]] == [1, 2, 3]
    assert body["lines"][1]["run_count"] == 1
    assert body["lines"][1]["runs"] == [2]


def test_cache_rows_from_before_tracing_are_rebuilt(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
    db_session: Session,
):
    from app.services.cache import explanation_hash, normalize_code

    old_hash = explanation_hash(normalize_code(THREE_LINES))
    db_session.add(
        Explanation(
            code_hash=old_hash,
            summary="An old explanation without a trace.",
            lines=[{"line": 1, "code": "a, b = 1, 2", "explanation": "Old."}],
            analysis={},
            provider="fake",
        )
    )
    db_session.commit()

    response = explain(client, auth_headers, THREE_LINES)

    assert response.status_code == 200
    body = response.json()
    assert body["cached"] is False
    assert body["trace_status"] == "ok"
    assert body["final_output"] == "3\n"
    assert db_session.scalar(select(func.count()).select_from(Explanation)) == 1
