"""Shared Unilever membership tables used for project / study collaborators."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

project_role_enum = Enum("editor", "viewer", name="project_role_enum", create_type=False)
study_role_enum = Enum(
    "admin", "editor", "viewer", name="study_role_enum", create_type=False
)


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "invited_email", name="uq_project_members_project_email"
        ),
        Index("idx_project_members_project_id_role", "project_id", "role"),
        Index("idx_project_members_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        project_role_enum, nullable=False, server_default="viewer"
    )
    invited_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        default=lambda: datetime.now(UTC),
    )


class StudyMember(Base):
    __tablename__ = "study_members"
    __table_args__ = (
        UniqueConstraint(
            "study_id", "invited_email", name="uq_study_members_study_email"
        ),
        Index("idx_study_members_study_id_role", "study_id", "role"),
        Index("idx_study_members_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # No SQLAlchemy FK to `studies` — that table is owned by Unilever and is not
    # mapped in this app's metadata (ORM flush would raise NoReferencedTableError).
    # Postgres still enforces the real FK on the shared database.
    study_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        study_role_enum, nullable=False, server_default="viewer"
    )
    invited_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        default=lambda: datetime.now(UTC),
    )
