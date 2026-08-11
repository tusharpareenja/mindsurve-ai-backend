"""Shared pytest fixtures — SQLite in-memory so tests do not need Postgres."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["APP_ENV"] = "test"
os.environ["DEBUG"] = "true"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-at-least-16-chars"
os.environ["FRONTEND_URL"] = "http://localhost:3000"
os.environ["COOKIE_SECURE"] = "false"
os.environ["COOKIE_SAMESITE"] = "lax"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""
os.environ["AZURE_STORAGE_CONNECTION_STRING"] = ""
os.environ["AZURE_STORAGE_CONTAINER_NAME"] = ""

from app.core.config import clear_settings_cache  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db, reset_engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    clear_settings_cache()
    reset_engine()

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # Expose session factory for tests that need direct DB access
    app.state.testing_session_factory = TestingSessionLocal  # type: ignore[attr-defined]

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    reset_engine()
    clear_settings_cache()
