"""History listing, detail, and ownership isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient


def register(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "history-password"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def explain(client: TestClient, headers: dict[str, str], code: str) -> dict:
    response = client.post("/api/explain", json={"code": code}, headers=headers)
    assert response.status_code == 200
    return response.json()


def test_history_lists_current_users_items_newest_first(
    client: TestClient, auth_headers: dict[str, str]
):
    first = explain(client, auth_headers, "print('first')\n")
    second = explain(client, auth_headers, "print('second')\n")

    response = client.get("/api/history", headers=auth_headers)

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [second["id"], first["id"]]
    assert items[0]["line_count"] == 1
    assert items[0]["provider"] == "fake"


def test_history_detail_returns_the_full_explanation(
    client: TestClient, auth_headers: dict[str, str]
):
    created = explain(client, auth_headers, "print('detail')\n")

    response = client.get(f"/api/history/{created['id']}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["code"] == "print('detail')\n"
    assert body["summary"] == created["summary"]
    assert body["lines"][0]["code"] == "print('detail')"


def test_a_user_cannot_read_another_users_item(
    client: TestClient, auth_headers: dict[str, str]
):
    owners_item = explain(client, auth_headers, "print('private')\n")
    other_headers = register(client, "other@example.com")

    response = client.get(
        f"/api/history/{owners_item['id']}", headers=other_headers
    )
    other_history = client.get("/api/history", headers=other_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert other_history.status_code == 200
    assert other_history.json()["items"] == []


def test_history_requires_authentication(client: TestClient):
    response = client.get("/api/history")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_missing_history_item_returns_404(
    client: TestClient, auth_headers: dict[str, str]
):
    response = client.get("/api/history/999999", headers=auth_headers)

    assert response.status_code == 404
