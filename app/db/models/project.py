"""Project ORM model — shared Unilever `projects` table + MindSurve extensions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.chat import Chat
    from app.db.models.user import User


class Project(Base):
    """
    Shared with Unilever Image Study.

    Unilever columns: id, name, description, creator_id, created_at, updated_at
    MindSurve extensions (nullable/defaulted): idea, workflow_type, status
    """

    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_creator_id_updated_at", "creator_id", "updated_at"),
        Index("idx_projects_creator_id_created_at", "creator_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # MindSurve-only extensions (added via MindSurve migration; Unilever ignores them)
    idea: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="beginner", server_default="beginner"
    )
    status: Mapped[str] = mapped_column(
        String(64), nullable=False, default="CREATED", server_default="CREATED"
    )
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

    user: Mapped[User] = relationship("User")
    chats: Mapped[list[Chat]] = relationship(
        "Chat",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def title(self) -> str:
        """API/frontend alias for Unilever `name`."""
        return self.name
