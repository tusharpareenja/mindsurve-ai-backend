"""Authentication API integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_refresh_token
from app.db.models.auth_session import AuthSession


REGISTER_PAYLOAD = {
    "name": "John Doe",
    "email": "john@example.com",
    "password": "securepass1",
}


def _register(client: TestClient, **overrides):
    payload = {**REGISTER_PAYLOAD, **overrides}
    return client.post("/api/v1/auth/register", json=payload)


def test_register_success(client: TestClient) -> None:
    response = _register(client)
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "john@example.com"
    assert data["user"]["name"] == "John Doe"
    assert "password" not in data
    assert "password_hash" not in data["user"]
    assert get_settings().REFRESH_COOKIE_NAME in response.cookies


def test_register_duplicate_email(client: TestClient) -> None:
    assert _register(client).status_code == 201
    response = _register(client)
    assert response.status_code == 409


def test_register_invalid_email(client: TestClient) -> None:
    response = _register(client, email="not-an-email")
    assert response.status_code == 422


def test_register_short_password(client: TestClient) -> None:
    response = _register(client, password="short")
    assert response.status_code == 422


def test_login_success(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "john@example.com", "password": "securepass1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["access_token"]
    assert data["user"]["email"] == "john@example.com"
    assert get_settings().REFRESH_COOKIE_NAME in response.cookies


def test_login_invalid_password(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "john@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_login_nonexistent_account(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "securepass1"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_me_with_valid_token(client: TestClient) -> None:
    reg = _register(client)
    token = reg.json()["access_token"]
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "john@example.com"
    assert "password_hash" not in response.json()


def test_me_without_auth(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_with_invalid_token(client: TestClient) -> None:
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_refresh_valid(client: TestClient) -> None:
    reg = _register(client)
    cookie_name = get_settings().REFRESH_COOKIE_NAME
    refresh_cookie = reg.cookies.get(cookie_name)
    assert refresh_cookie

    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    assert response.json()["access_token"]
    # Rotated cookie
    assert response.cookies.get(cookie_name)


def test_refresh_invalid_token(client: TestClient) -> None:
    client.cookies.set(
        get_settings().REFRESH_COOKIE_NAME,
        "invalid-refresh-token",
        path=get_settings().REFRESH_COOKIE_PATH,
    )
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401


def test_refresh_revoked_token(client: TestClient) -> None:
    reg = _register(client)
    cookie_name = get_settings().REFRESH_COOKIE_NAME
    raw = reg.cookies.get(cookie_name)
    assert raw

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 200

    # Re-attach the old (now revoked) refresh token
    client.cookies.set(cookie_name, raw, path=get_settings().REFRESH_COOKIE_PATH)
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401


def test_refresh_expired_token(client: TestClient) -> None:
    reg = _register(client)
    cookie_name = get_settings().REFRESH_COOKIE_NAME
    raw = reg.cookies.get(cookie_name)
    assert raw

    factory = client.app.state.testing_session_factory  # type: ignore[attr-defined]
    db = factory()
    try:
        session = db.scalars(
            select(AuthSession).where(AuthSession.refresh_token_hash == hash_refresh_token(raw))
        ).first()
        assert session is not None
        session.expires_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    finally:
        db.close()

    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401


def test_logout_invalidates_refresh(client: TestClient) -> None:
    reg = _register(client)
    cookie_name = get_settings().REFRESH_COOKIE_NAME
    assert reg.cookies.get(cookie_name)

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    assert logout.json()["message"] == "Logged out"

    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401


def test_access_token_type_enforced(client: TestClient) -> None:
    reg = _register(client)
    user_id = reg.json()["user"]["id"]
    # Craft token with wrong type by calling jwt directly
    import jwt
    from app.core.config import get_settings as gs

    settings = gs()
    bad = jwt.encode(
        {
            "sub": user_id,
            "type": "refresh",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {bad}"},
    )
    assert response.status_code == 401


def test_me_with_expired_access_token(client: TestClient) -> None:
    reg = _register(client)
    user_id = reg.json()["user"]["id"]
    from uuid import UUID

    expired = create_access_token(
        user_id=UUID(user_id),
        email=reg.json()["user"]["email"],
        expires_delta=timedelta(seconds=-1),
    )
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert response.status_code == 401
