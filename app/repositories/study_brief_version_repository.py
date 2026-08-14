"""Persistence for study-brief version snapshots."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.study_brief_version import StudyBriefVersion


class StudyBriefVersionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_for_chat(self, chat_id: UUID) -> list[StudyBriefVersion]:
        stmt = (
            select(StudyBriefVersion)
            .where(StudyBriefVersion.chat_id == chat_id)
            .order_by(StudyBriefVersion.version_number.asc())
        )
        return list(self.db.scalars(stmt).all())

    def get(self, chat_id: UUID, version: int) -> StudyBriefVersion | None:
        stmt = select(StudyBriefVersion).where(
            StudyBriefVersion.chat_id == chat_id,
            StudyBriefVersion.version_number == version,
        )
        return self.db.scalars(stmt).first()

    def latest(self, chat_id: UUID) -> StudyBriefVersion | None:
        stmt = (
            select(StudyBriefVersion)
            .where(StudyBriefVersion.chat_id == chat_id)
            .order_by(StudyBriefVersion.version_number.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def max_version(self, chat_id: UUID) -> int:
        stmt = select(func.max(StudyBriefVersion.version_number)).where(
            StudyBriefVersion.chat_id == chat_id
        )
        return int(self.db.scalar(stmt) or 0)

    def add(self, row: StudyBriefVersion) -> StudyBriefVersion:
        self.db.add(row)
        self.db.flush()
        return row
