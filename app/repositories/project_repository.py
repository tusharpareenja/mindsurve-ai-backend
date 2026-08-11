"""Project / chat / message persistence — keep queries indexed and N+1-free."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.db.models.chat import Chat, ChatMessage
from app.db.models.project import Project


class ProjectRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Projects (shared Unilever table: creator_id + name) ───────────────

    def list_projects_for_user(self, user_id: UUID) -> list[Project]:
        stmt = (
            select(Project)
            .where(Project.creator_id == user_id)
            .order_by(Project.updated_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_project_for_user(self, project_id: UUID, user_id: UUID) -> Project | None:
        stmt = select(Project).where(
            Project.id == project_id,
            Project.creator_id == user_id,
        )
        return self.db.scalars(stmt).first()

    def create_project(self, *, user_id: UUID, title: str) -> Project:
        now = datetime.now(UTC)
        project = Project(
            creator_id=user_id,
            name=title,
            description="",
            workflow_type="beginner",
            status="CREATED",
            created_at=now,
            updated_at=now,
        )
        self.db.add(project)
        self.db.flush()
        return project

    def save_project(self, project: Project) -> Project:
        project.updated_at = datetime.now(UTC)
        self.db.add(project)
        self.db.flush()
        return project

    def delete_project(self, project: Project) -> None:
        self.db.delete(project)
        self.db.flush()

    # ── Chats ─────────────────────────────────────────────────────────────

    def list_chats_for_project(self, project_id: UUID) -> list[Chat]:
        stmt = (
            select(Chat)
            .where(Chat.project_id == project_id)
            .order_by(Chat.updated_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_chats_for_user(self, user_id: UUID) -> list[Chat]:
        """All chats owned by the user (via project), one query + join."""
        stmt = (
            select(Chat)
            .join(Project, Chat.project_id == Project.id)
            .where(Project.creator_id == user_id)
            .order_by(Chat.updated_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_chat_for_user(self, chat_id: UUID, user_id: UUID) -> Chat | None:
        stmt = (
            select(Chat)
            .join(Project, Chat.project_id == Project.id)
            .where(Chat.id == chat_id, Project.creator_id == user_id)
        )
        return self.db.scalars(stmt).first()

    def create_chat(self, *, project_id: UUID, title: str) -> Chat:
        now = datetime.now(UTC)
        chat = Chat(
            project_id=project_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        self.db.add(chat)
        self.db.flush()
        return chat

    def save_chat(self, chat: Chat) -> Chat:
        chat.updated_at = datetime.now(UTC)
        self.db.add(chat)
        self.db.flush()
        return chat

    def delete_chat(self, chat: Chat) -> None:
        self.db.delete(chat)
        self.db.flush()

    def latest_message_previews(self, chat_ids: list[UUID]) -> dict[UUID, str]:
        """One query for latest message content per chat — no N+1."""
        if not chat_ids:
            return {}

        max_created = (
            select(
                ChatMessage.chat_id.label("chat_id"),
                func.max(ChatMessage.created_at).label("max_created"),
            )
            .where(ChatMessage.chat_id.in_(chat_ids))
            .group_by(ChatMessage.chat_id)
            .subquery()
        )

        stmt = select(ChatMessage.chat_id, ChatMessage.content).join(
            max_created,
            and_(
                ChatMessage.chat_id == max_created.c.chat_id,
                ChatMessage.created_at == max_created.c.max_created,
            ),
        )
        rows = self.db.execute(stmt).all()
        return {chat_id: content for chat_id, content in rows}

    # ── Messages ──────────────────────────────────────────────────────────

    def list_message_page(
        self,
        chat_id: UUID,
        *,
        limit: int,
        before_created_at: datetime | None = None,
        before_id: UUID | None = None,
    ) -> tuple[list[ChatMessage], bool]:
        """Return one newest-first DB page, exposed in chronological order."""
        conditions = [ChatMessage.chat_id == chat_id]
        if before_created_at is not None and before_id is not None:
            conditions.append(
                or_(
                    ChatMessage.created_at < before_created_at,
                    and_(
                        ChatMessage.created_at == before_created_at,
                        ChatMessage.id < before_id,
                    ),
                )
            )

        stmt = (
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit + 1)
        )
        rows = list(self.db.scalars(stmt).all())
        has_more = len(rows) > limit
        page = rows[:limit]
        page.reverse()
        return page, has_more

    def list_recent_messages(
        self, chat_id: UUID, *, limit: int = 24
    ) -> list[ChatMessage]:
        """Bound LLM context so long chats never load fully into memory."""
        messages, _ = self.list_message_page(chat_id, limit=limit)
        return messages

    def create_message(
        self,
        *,
        chat_id: UUID,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> ChatMessage:
        msg = ChatMessage(
            chat_id=chat_id,
            role=role,
            content=content,
            metadata_json=metadata,
            created_at=datetime.now(UTC),
        )
        self.db.add(msg)
        self.db.flush()
        return msg

    def delete_chats_for_project(self, project_id: UUID) -> None:
        self.db.execute(delete(Chat).where(Chat.project_id == project_id))
        self.db.flush()
