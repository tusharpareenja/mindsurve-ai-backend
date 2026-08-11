"""Persistence helpers for users and auth sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.auth_session import AuthSession
from app.db.models.user import User


class AuthRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_user_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower().strip())
        return self.db.scalars(stmt).first()

    def get_user_by_id(self, user_id: UUID) -> User | None:
        return self.db.get(User, user_id)

    def create_user(self, *, name: str, email: str, password_hash: str) -> User:
        cleaned_name = name.strip()
        if len(cleaned_name) < 2:
            cleaned_name = (cleaned_name + " User").strip()[:100]
        user = User(
            name=cleaned_name[:100],
            email=email.lower().strip(),
            password_hash=password_hash,
            is_active=True,
            is_verified=False,
            dashboard_onboarding_completed=False,
            dashboard_onboarding_skipped=False,
            create_study_onboarding_completed=False,
            create_study_onboarding_skipped=False,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def touch_last_login(self, user: User) -> None:
        user.last_login = datetime.now(UTC)
        user.updated_at = datetime.now(UTC)
        self.db.add(user)
        self.db.flush()

    def create_session(
        self,
        *,
        user_id: UUID,
        refresh_token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
        ip_address: str | None,
    ) -> AuthSession:
        session = AuthSession(
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.db.add(session)
        self.db.flush()
        return session

    def get_session_by_token_hash(self, token_hash: str) -> AuthSession | None:
        stmt = select(AuthSession).where(AuthSession.refresh_token_hash == token_hash)
        return self.db.scalars(stmt).first()

    def revoke_session(self, session: AuthSession) -> None:
        if session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            session.updated_at = datetime.now(UTC)
            self.db.add(session)
            self.db.flush()

    def rotate_session_token(
        self,
        session: AuthSession,
        *,
        new_token_hash: str,
        expires_at: datetime,
    ) -> AuthSession:
        session.refresh_token_hash = new_token_hash
        session.expires_at = expires_at
        session.updated_at = datetime.now(UTC)
        self.db.add(session)
        self.db.flush()
        return session
