"""Immutable snapshots of a chat's study brief (draft version history).

Matches the shared Postgres table (version_number / snapshot_json), not a
fresh MindSurve-only schema.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid

from app.db.base import Base


class StudyBriefVersion(Base):
    __tablename__ = "study_brief_versions"
    __table_args__ = (
        UniqueConstraint(
            "chat_id", "version_number", name="uq_study_brief_versions_chat_version"
        ),
        Index("ix_study_brief_versions_chat_id_created_at", "chat_id", "created_at"),
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
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="ai")
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid().with_variant(PGUUID(as_uuid=True), "postgresql"),
        nullable=True,
    )
    restored_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid().with_variant(PGUUID(as_uuid=True), "postgresql"),
        nullable=True,
    )
    operations_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    changed_paths: Mapped[list[str] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid().with_variant(PGUUID(as_uuid=True), "postgresql"),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid().with_variant(PGUUID(as_uuid=True), "postgresql"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )

    @property
    def version(self) -> int:
        return self.version_number

    @property
    def brief_json(self) -> dict[str, Any]:
        return self.snapshot_json or {}

    @property
    def changed_fields(self) -> list[str]:
        return list(self.changed_paths or [])
