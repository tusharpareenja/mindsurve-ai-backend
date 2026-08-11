"""SQLAlchemy engine and session factory.

The engine is created lazily so the application can start without a configured
DATABASE_URL. No connection is opened at import time.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _create_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def get_engine() -> Engine:
    """Return the shared SQLAlchemy engine, creating it on first use."""
    global _engine, _SessionLocal

    if _engine is not None:
        return _engine

    settings = get_settings()
    if not settings.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set it in the environment or .env "
            "before using the database."
        )

    _engine = _create_engine(settings.DATABASE_URL)
    _SessionLocal = sessionmaker(
        bind=_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the session factory, ensuring the engine exists."""
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def reset_engine() -> None:
    """Dispose and clear the cached engine (used by tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def configure_engine(database_url: str) -> Engine:
    """Force-configure the engine with an explicit URL (tests)."""
    global _engine, _SessionLocal
    reset_engine()
    _engine = _create_engine(database_url)
    _SessionLocal = sessionmaker(
        bind=_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    return _engine
