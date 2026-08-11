"""Password hashing and JWT helpers — JWT payload aligned with Unilever."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import get_settings


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (compatible with Unilever passlib hashes)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_refresh_token() -> str:
    """Return a high-entropy opaque refresh token (store only its hash)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 hash of a refresh token for database storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(
    *,
    user_id: UUID,
    email: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token compatible with Unilever (`sub`, `email`, `type`, `exp`)."""
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> UUID:
    """Validate an access token (MindSurve or Unilever-issued) and return user id."""
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["sub", "exp", "type"]},
    )
    if payload.get("type") != "access":
        raise InvalidTokenError("Invalid token type")
    try:
        return UUID(str(payload["sub"]))
    except (ValueError, TypeError) as exc:
        raise InvalidTokenError("Invalid subject") from exc


def refresh_token_expiry() -> datetime:
    """UTC expiry timestamp for a new refresh session."""
    settings = get_settings()
    return datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
