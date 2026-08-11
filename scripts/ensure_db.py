"""Create the application database if it does not exist (local bootstrap)."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, text

from app.core.config import clear_settings_cache, get_settings


def main() -> None:
    clear_settings_cache()
    url = get_settings().DATABASE_URL
    if not url:
        print("No DATABASE_URL configured; skip")
        return

    normalized = url.replace("postgresql+psycopg://", "postgresql://")
    parsed = urlparse(normalized)
    db_name = (parsed.path or "/mindsurve").lstrip("/") or "mindsurve"
    admin = urlunparse(parsed._replace(path="/postgres")).replace(
        "postgresql://", "postgresql+psycopg://"
    )

    eng = create_engine(admin, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": db_name},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                print(f"Created database {db_name}")
            else:
                print(f"Database exists: {db_name}")
    finally:
        eng.dispose()


if __name__ == "__main__":
    main()
