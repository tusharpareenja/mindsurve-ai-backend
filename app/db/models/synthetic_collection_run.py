"""MindSurve-owned orchestration for Unilever synthetic respondent jobs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid

from app.db.base import Base


class SyntheticCollectionRun(Base):
    __tablename__ = "synthetic_collection_runs"
    __table_args__ = (
        Index("ix_synthetic_collection_runs_chat_id_created_at", "chat_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid().with_variant(PGUUID(as_uuid=True), "postgresql"),
        primary_key=True,
        default=uuid.uuid4,
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(
        Uuid().with_variant(PGUUID(as_uuid=True), "postgresql"),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid().with_variant(PGUUID(as_uuid=True), "postgresql"),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid().with_variant(PGUUID(as_uuid=True), "postgresql"),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    study_id: Mapped[uuid.UUID] = mapped_column(
        Uuid().with_variant(PGUUID(as_uuid=True), "postgresql"),
        nullable=False,
        index=True,
    )
    upstream_job_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="ai")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    respondents_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    respondents_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
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
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
