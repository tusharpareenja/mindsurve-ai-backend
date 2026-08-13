"""Persistence for synthetic collection runs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.synthetic_collection_run import SyntheticCollectionRun


class SyntheticCollectionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, run: SyntheticCollectionRun) -> SyntheticCollectionRun:
        self.db.add(run)
        self.db.flush()
        return run

    def save(self, run: SyntheticCollectionRun) -> SyntheticCollectionRun:
        self.db.add(run)
        self.db.flush()
        return run

    def get(self, run_id: UUID) -> SyntheticCollectionRun | None:
        return self.db.get(SyntheticCollectionRun, run_id)

    def latest_for_chat(self, chat_id: UUID) -> SyntheticCollectionRun | None:
        stmt = (
            select(SyntheticCollectionRun)
            .where(SyntheticCollectionRun.chat_id == chat_id)
            .order_by(SyntheticCollectionRun.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def active_for_chat(self, chat_id: UUID) -> SyntheticCollectionRun | None:
        stmt = (
            select(SyntheticCollectionRun)
            .where(
                SyntheticCollectionRun.chat_id == chat_id,
                SyntheticCollectionRun.status.in_(("queued", "running")),
            )
            .order_by(SyntheticCollectionRun.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()
