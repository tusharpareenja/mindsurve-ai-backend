"""Ensure .env has a working local DATABASE_URL (never prints credentials)."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

CANDIDATES = [
    "postgresql+psycopg://postgres:postgres@localhost:5432/mindsurve",
    "postgresql+psycopg://postgres@localhost:5432/mindsurve",
    "postgresql+psycopg://postgres:admin@localhost:5432/mindsurve",
]


def load_env_keys(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def try_connect(url: str) -> bool:
    # Connect to postgres DB first to create mindsurve if needed
    admin = url.rsplit("/", 1)[0] + "/postgres"
    try:
        eng = create_engine(admin, isolation_level="AUTOCOMMIT")
        with eng.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = 'mindsurve'")
            ).scalar()
            if not exists:
                conn.execute(text('CREATE DATABASE "mindsurve"'))
        eng.dispose()
        eng2 = create_engine(url)
        with eng2.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng2.dispose()
        return True
    except Exception:
        return False


def upsert_database_url(url: str) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    written = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") and "DATABASE_URL=" in stripped:
            new_lines.append(f"DATABASE_URL={url}")
            written = True
            continue
        if stripped.startswith("DATABASE_URL="):
            new_lines.append(f"DATABASE_URL={url}")
            written = True
            continue
        new_lines.append(line)

    if not written:
        new_lines.append(f"DATABASE_URL={url}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> None:
    existing = load_env_keys(ENV_PATH).get("DATABASE_URL") or os.environ.get("DATABASE_URL")
    if existing and try_connect(existing):
        print("DATABASE_URL already works")
        return

    for candidate in CANDIDATES:
        if try_connect(candidate):
            upsert_database_url(candidate)
            print("Configured working local DATABASE_URL in .env")
            return

    print(
        "Could not auto-configure DATABASE_URL. "
        "Set DATABASE_URL in .env to your Postgres instance, then run: alembic upgrade head"
    )


if __name__ == "__main__":
    main()
