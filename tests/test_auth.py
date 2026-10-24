"""Registration, login, JWT, and protected-route behavior."""

from __future__ import annotations

from fastapi.testclient import TestClient

PASSWORD = "a-secure-password"


def register(client: TestClient, email: str = "ada@example.com"):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD},
    )


def test_register_returns_token_and_normalizes_email(client: TestClient):
    response = register(client, "Ada@Example.com")

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "ada@example.com"
    assert "password" not in body["user"]


def test_duplicate_email_is_rejected_case_insensitively(client: TestClient):
    assert register(client, "ada@example.com").status_code == 201

    response = register(client, "ADA@example.com")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


def test_login_returns_a_working_token(client: TestClient):
    register(client)
    response = client.post(
        "/api/auth/login",
        json={"email": "ada@example.com", "password": PASSWORD},
    )

    assert response.status_code == 200
    token = response.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"


def test_login_rejects_wrong_password(client: TestClient):
    register(client)
    response = client.post(
        "/api/auth/login",
        json={"email": "ada@example.com", "password": "definitely-wrong"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_protected_route_requires_token(client: TestClient):
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


def test_protected_route_rejects_malformed_token(client: TestClient):
    response = client.get(
        "/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_register_validates_short_password(client: TestClient):
    response = client.post(
        "/api/auth/register",
        json={"email": "ada@example.com", "password": "short"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["fields"]
