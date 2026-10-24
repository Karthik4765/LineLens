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

from app.database import Base, SessionLocal, engine
from app.llm.fake import FakeProvider
from app.main import app
from app.services.explainer import Explainer, get_explainer
from app.services.rate_limiter import SlidingWindowRateLimiter, get_rate_limiter
from app.routers.run import get_run_rate_limiter

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


@pytest.fixture(autouse=True)
def rate_limiter():
    """Give each test an isolated limiter with the production-sized defaults."""

    limiter = SlidingWindowRateLimiter(limit=10, window_seconds=60)
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    yield limiter
    app.dependency_overrides.pop(get_rate_limiter, None)


@pytest.fixture(autouse=True)
def run_rate_limiter():
    """Keep code-execution limits separate from AI-explanation limits."""

    limiter = SlidingWindowRateLimiter(limit=100, window_seconds=60)
    app.dependency_overrides[get_run_rate_limiter] = lambda: limiter
    yield limiter
    app.dependency_overrides.pop(get_run_rate_limiter, None)


@pytest.fixture
def fake_provider():
    """Install a fresh fake provider for a test and expose it for call counts."""

    provider = FakeProvider()
    app.dependency_overrides[get_explainer] = lambda: Explainer([provider])
    yield provider
    app.dependency_overrides.pop(get_explainer, None)


@pytest.fixture
def set_explainer():
    """Install a custom explainer (used for retry and fallback tests)."""

    def install(explainer: Explainer) -> Explainer:
        app.dependency_overrides[get_explainer] = lambda: explainer
        return explainer

    yield install
    app.dependency_overrides.pop(get_explainer, None)


@pytest.fixture
def db_session():
    with SessionLocal() as session:
        yield session
