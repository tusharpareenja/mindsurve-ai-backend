"""Authentication business logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from jwt.exceptions import InvalidTokenError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from app.db.models.user import User
from app.repositories.auth_repository import AuthRepository


class AuthError(Exception):
    """Base authentication error with a safe client message."""

    def __init__(self, message: str, *, status_code: int = 401) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ConflictError(AuthError):
    def __init__(self, message: str = "An account with this email already exists.") -> None:
        super().__init__(message, status_code=409)


@dataclass(frozen=True)
class AuthResult:
    user: User
    access_token: str
    refresh_token: str


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = AuthRepository(db)

    def register(
        self,
        *,
        name: str,
        email: str,
        password: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> AuthResult:
        if self.repo.get_user_by_email(email):
            raise ConflictError()

        password_hash = hash_password(password)
        try:
            user = self.repo.create_user(
                name=name,
                email=email,
                password_hash=password_hash,
            )
            from app.services.collaborator_service import CollaboratorService

            CollaboratorService(self.db).claim_pending_invites(user)
            result = self._issue_tokens(
                user,
                user_agent=user_agent,
                ip_address=ip_address,
            )
            self.db.commit()
            self.db.refresh(user)
            return result
        except IntegrityError as exc:
            self.db.rollback()
            raise ConflictError() from exc
        except Exception:
            self.db.rollback()
            raise

    def login(
        self,
        *,
        email: str,
        password: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> AuthResult:
        user = self.repo.get_user_by_email(email)
        # Constant-ish failure message — do not reveal which field failed.
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise AuthError("Invalid email or password.")

        try:
            from app.services.collaborator_service import CollaboratorService

            CollaboratorService(self.db).claim_pending_invites(user)
            self.repo.touch_last_login(user)
            result = self._issue_tokens(
                user,
                user_agent=user_agent,
                ip_address=ip_address,
            )
            self.db.commit()
            self.db.refresh(user)
            return result
        except Exception:
            self.db.rollback()
            raise

    def refresh(
        self,
        *,
        refresh_token: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> AuthResult:
        token_hash = hash_refresh_token(refresh_token)
        session = self.repo.get_session_by_token_hash(token_hash)
        if session is None:
            raise AuthError("Invalid or expired refresh token.")

        now = datetime.now(UTC)
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        if session.revoked_at is not None or expires_at <= now:
            raise AuthError("Invalid or expired refresh token.")

        user = self.repo.get_user_by_id(session.user_id)
        if user is None or not user.is_active:
            raise AuthError("Invalid or expired refresh token.")

        try:
            # Rotate refresh token to limit replay usefulness.
            new_refresh = generate_refresh_token()
            self.repo.rotate_session_token(
                session,
                new_token_hash=hash_refresh_token(new_refresh),
                expires_at=refresh_token_expiry(),
            )
            if user_agent:
                session.user_agent = user_agent
            if ip_address:
                session.ip_address = ip_address
            access_token = create_access_token(user_id=user.id, email=user.email)
            self.db.commit()
            self.db.refresh(user)
            return AuthResult(user=user, access_token=access_token, refresh_token=new_refresh)
        except Exception:
            self.db.rollback()
            raise

    def logout(self, *, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        token_hash = hash_refresh_token(refresh_token)
        session = self.repo.get_session_by_token_hash(token_hash)
        if session is None:
            return
        try:
            self.repo.revoke_session(session)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def get_current_user_from_access_token(self, token: str) -> User:
        try:
            user_id = decode_access_token(token)
        except InvalidTokenError as exc:
            raise AuthError("Could not validate credentials.") from exc

        user = self.repo.get_user_by_id(user_id)
        if user is None or not user.is_active:
            raise AuthError("Could not validate credentials.")
        return user

    def _issue_tokens(
        self,
        user: User,
        *,
        user_agent: str | None,
        ip_address: str | None,
    ) -> AuthResult:
        refresh_token = generate_refresh_token()
        self.repo.create_session(
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(refresh_token),
            expires_at=refresh_token_expiry(),
            user_agent=user_agent,
            ip_address=ip_address,
        )
        access_token = create_access_token(user_id=user.id, email=user.email)
        return AuthResult(user=user, access_token=access_token, refresh_token=refresh_token)


def get_user_id_hint(user_id: UUID) -> str:
    """Non-sensitive helper for logs (truncated id only)."""
    return str(user_id)[:8]
