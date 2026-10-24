"""Shared pytest fixtures. All tests run against a temporary SQLite database."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_DB_PATH = Path(tempfile.gettempdir()) / "linelens_pytest.db"

# Configuration is read when app modules are first imported, so establish the
# test environment before importing the application.
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["JWT_SECRET"] = "test-only-secret-that-is-long-enough"
os.environ["LLM_PROVIDER"] = "fake"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app

if TEST_DB_PATH.exists():
    TEST_DB_PATH.unlink()


@pytest.fixture(autouse=True)
def clean_database():
    """Give every test an empty schema."""

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def user_token(client: TestClient) -> str:
    """Register one user and return a bearer token."""

    response = client.post(
        "/api/auth/register",
        json={"email": "learner@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 201
    return response.json()["access_token"]


@pytest.fixture
def auth_headers(user_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token}"}
