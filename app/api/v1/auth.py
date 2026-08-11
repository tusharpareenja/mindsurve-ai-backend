"""Authentication HTTP endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.config import Settings, get_settings
from app.db.models.user import User
from app.dependencies.auth import get_auth_service, get_current_user
from app.schemas.auth import (
    AccessTokenResponse,
    AuthTokenResponse,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    UserPublic,
)
from app.services.auth_service import AuthError, AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    return user_agent, ip


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    expires = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.COOKIE_SECURE or settings.is_production,
        samesite=settings.COOKIE_SAMESITE,  # type: ignore[arg-type]
        path=settings.REFRESH_COOKIE_PATH,
        max_age=max_age,
        expires=expires,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        path=settings.REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.COOKIE_SECURE or settings.is_production,
        samesite=settings.COOKIE_SAMESITE,  # type: ignore[arg-type]
    )


def _raise_auth_error(exc: AuthError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "/register",
    response_model=AuthTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthTokenResponse:
    user_agent, ip = _client_meta(request)
    try:
        result = auth.register(
            name=body.name,
            email=str(body.email),
            password=body.password,
            user_agent=user_agent,
            ip_address=ip,
        )
    except AuthError as exc:
        _raise_auth_error(exc)

    _set_refresh_cookie(response, result.refresh_token, settings)
    return AuthTokenResponse(
        access_token=result.access_token,
        user=UserPublic.model_validate(result.user),
    )


@router.post("/login", response_model=AuthTokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthTokenResponse:
    user_agent, ip = _client_meta(request)
    try:
        result = auth.login(
            email=str(body.email),
            password=body.password,
            user_agent=user_agent,
            ip_address=ip,
        )
    except AuthError as exc:
        _raise_auth_error(exc)

    _set_refresh_cookie(response, result.refresh_token, settings)
    return AuthTokenResponse(
        access_token=result.access_token,
        user=UserPublic.model_validate(result.user),
    )


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh(
    request: Request,
    response: Response,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AccessTokenResponse:
    refresh_token = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    user_agent, ip = _client_meta(request)
    try:
        result = auth.refresh(
            refresh_token=refresh_token,
            user_agent=user_agent,
            ip_address=ip,
        )
    except AuthError as exc:
        _clear_refresh_cookie(response, settings)
        _raise_auth_error(exc)

    _set_refresh_cookie(response, result.refresh_token, settings)
    return AccessTokenResponse(access_token=result.access_token)


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    refresh_token = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    auth.logout(refresh_token=refresh_token)
    _clear_refresh_cookie(response, settings)
    return MessageResponse(message="Logged out")


@router.get("/me", response_model=UserPublic)
def me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(current_user)
