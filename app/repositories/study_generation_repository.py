"""Persistence for study generation runs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.study_generation_run import StudyGenerationRun


class StudyGenerationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, run: StudyGenerationRun) -> StudyGenerationRun:
        self.db.add(run)
        self.db.flush()
        return run

    def save(self, run: StudyGenerationRun) -> StudyGenerationRun:
        self.db.add(run)
        self.db.flush()
        return run

    def get(self, run_id: UUID) -> StudyGenerationRun | None:
        return self.db.get(StudyGenerationRun, run_id)

    def latest_for_chat(self, chat_id: UUID) -> StudyGenerationRun | None:
        stmt = (
            select(StudyGenerationRun)
            .where(StudyGenerationRun.chat_id == chat_id)
            .order_by(StudyGenerationRun.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def active_for_chat(self, chat_id: UUID) -> StudyGenerationRun | None:
        stmt = (
            select(StudyGenerationRun)
            .where(
                StudyGenerationRun.chat_id == chat_id,
                StudyGenerationRun.status.in_(
                    ("queued", "generating", "saving")
                ),
            )
            .order_by(StudyGenerationRun.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def max_revision(self, chat_id: UUID) -> int:
        latest = self.latest_for_chat(chat_id)
        return latest.revision if latest else 0
