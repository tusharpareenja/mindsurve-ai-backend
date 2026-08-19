"""Project and chat business logic."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import AppError, NotFoundError
from app.db.models.chat import Chat, ChatMessage
from app.db.models.project import Project
from app.db.models.user import User
from app.repositories.project_repository import ProjectRepository
from app.schemas.project import ChatOut, MessageOut, MessagePageOut

INBOX_TITLE = "Personal"
INBOX_WORKFLOW = "inbox"


def is_inbox_project(project: Project) -> bool:
    return (project.workflow_type or "") == INBOX_WORKFLOW


class ProjectService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = ProjectRepository(db)

    # ── Projects ──────────────────────────────────────────────────────────

    def list_projects(self, user: User) -> list[Project]:
        return self.repo.list_projects_for_user(user.id)

    def get_project(self, user: User, project_id: UUID) -> Project:
        project = self.repo.get_project_for_user(project_id, user.id)
        if project is None:
            raise NotFoundError("Project not found.")
        if not is_inbox_project(project):
            try:
                from app.services.collaborator_service import CollaboratorService

                # Always commit after repair so study_members sync persists too.
                CollaboratorService(self.db).repair_project_study_links(project.id)
                self.db.commit()
            except Exception:
                self.db.rollback()
                import logging

                logging.getLogger(__name__).exception(
                    "Study link repair failed for project %s", project.id
                )
        return project

    def ensure_inbox(self, user: User) -> Project:
        """Hidden personal workspace for chat-first (no user-created project)."""
        existing = self.repo.get_inbox_for_user(user.id)
        if existing is not None:
            return existing
        try:
            project = self.repo.create_project(
                user_id=user.id,
                title=INBOX_TITLE,
                workflow_type=INBOX_WORKFLOW,
                description="MindSurve personal chats (hidden inbox)",
            )
            self.db.commit()
            self.db.refresh(project)
            return project
        except Exception:
            self.db.rollback()
            existing = self.repo.get_inbox_for_user(user.id)
            if existing is not None:
                return existing
            raise

    def create_project(self, user: User, *, title: str) -> Project:
        from app.core.exceptions import AppError

        trimmed = title.strip()
        if not trimmed:
            raise AppError("Project title is required.", status_code=422)

        try:
            project = self.repo.create_project(
                user_id=user.id,
                title=trimmed,
                workflow_type="beginner",
            )
            self.db.commit()
            self.db.refresh(project)
            return project
        except Exception:
            self.db.rollback()
            raise

    def rename_project(self, user: User, project_id: UUID, *, title: str) -> Project:
        from app.core.exceptions import AppError

        trimmed = title.strip()
        if not trimmed:
            raise AppError("Project title is required.", status_code=422)

        project = self.get_project(user, project_id)
        if is_inbox_project(project):
            raise AppError("Personal chats can’t be renamed.", status_code=422)

        try:
            project.name = trimmed
            self.repo.save_project(project)
            self.db.commit()
            self.db.refresh(project)
            return project
        except Exception:
            self.db.rollback()
            raise

    def delete_project(self, user: User, project_id: UUID) -> None:
        project = self.repo.get_owned_project(project_id, user.id)
        if project is None:
            # Distinguish not found vs not owner when user can see the project
            accessible = self.repo.get_project_for_user(project_id, user.id)
            if accessible is not None:
                raise AppError(
                    "Only the project owner can delete this project.",
                    status_code=403,
                )
            raise NotFoundError("Project not found.")
        if is_inbox_project(project):
            raise AppError(
                "Personal chats can’t be deleted as a project. Delete individual chats instead.",
                status_code=422,
            )
        try:
            # DB CASCADE removes chats + messages
            self.repo.delete_project(project)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    # ── Chats ─────────────────────────────────────────────────────────────

    def list_chats_for_project(self, user: User, project_id: UUID) -> list[ChatOut]:
        self.get_project(user, project_id)  # ownership check
        chats = self.repo.list_chats_for_project(project_id)
        return self._chats_with_previews(chats)

    def list_all_chats(self, user: User) -> list[ChatOut]:
        chats = self.repo.list_chats_for_user(user.id)
        return self._chats_with_previews(chats)

    def get_chat(self, user: User, chat_id: UUID) -> Chat:
        chat = self.repo.get_chat_for_user(chat_id, user.id)
        if chat is None:
            raise NotFoundError("Chat not found.")
        return chat

    def create_chat(
        self,
        user: User,
        project_id: UUID,
        *,
        title: str | None = None,
    ) -> ChatOut:
        project = self.get_project(user, project_id)
        chat_title = (title or "New Chat").strip() or "New Chat"
        try:
            chat = self.repo.create_chat(project_id=project.id, title=chat_title)
            # Touch project updated_at so project list ordering stays useful
            self.repo.save_project(project)
            self.db.commit()
            self.db.refresh(chat)
            return ChatOut.model_validate(chat)
        except Exception:
            self.db.rollback()
            raise

    def start_chat_with_message(
        self,
        user: User,
        project_id: UUID,
        *,
        content: str,
    ) -> tuple[ChatOut, MessageOut]:
        project = self.get_project(user, project_id)
        body = content.strip()
        from app.core.exceptions import AppError

        if not body:
            raise AppError("Message content is required.", status_code=422)

        try:
            chat = self.repo.create_chat(project_id=project.id, title="New Chat")
            msg = self.repo.create_message(
                chat_id=chat.id,
                role="user",
                content=body,
            )
            self.repo.save_chat(chat)
            self.repo.save_project(project)
            self.db.commit()
            self.db.refresh(chat)
            self.db.refresh(msg)
            chat_out = ChatOut.model_validate(chat)
            chat_out.last_message_preview = msg.content
            return chat_out, self._message_out(msg)
        except Exception:
            self.db.rollback()
            raise

    def start_home_chat(
        self,
        user: User,
        *,
        content: str,
    ) -> tuple[ChatOut, MessageOut]:
        """Chat-first entry: start a study chat without creating a named project."""
        inbox = self.ensure_inbox(user)
        return self.start_chat_with_message(user, inbox.id, content=content)

    def rename_chat(self, user: User, chat_id: UUID, *, title: str) -> ChatOut:
        return self.update_chat(user, chat_id, title=title)

    def update_chat(
        self,
        user: User,
        chat_id: UUID,
        *,
        title: str | None = None,
        project_id: UUID | None = None,
    ) -> ChatOut:
        if title is None and project_id is None:
            raise AppError("Provide a title or a project to update.", status_code=422)

        chat = self.get_chat(user, chat_id)
        old_project = self.repo.get_project_for_user(chat.project_id, user.id)
        target_project = None
        if project_id is not None:
            target_project = self.get_project(user, project_id)

        try:
            if title is not None:
                trimmed = title.strip()
                if not trimmed:
                    raise AppError("Chat title is required.", status_code=422)
                chat.title = trimmed
            if target_project is not None:
                chat.project_id = target_project.id
                self.repo.save_project(target_project)
                if old_project is not None and old_project.id != target_project.id:
                    self.repo.save_project(old_project)
                # Studies created while chat was in inbox have project_id=null —
                # affiliate them with the destination named project for Unilever.
                if not is_inbox_project(target_project):
                    from app.services.collaborator_service import CollaboratorService

                    CollaboratorService(self.db).ensure_chat_studies_linked(
                        chat, target_project
                    )
            self.repo.save_chat(chat)
            self.db.commit()
            self.db.refresh(chat)
            return ChatOut.model_validate(chat)
        except AppError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

    def delete_chat(self, user: User, chat_id: UUID) -> None:
        chat = self.get_chat(user, chat_id)
        try:
            self.repo.delete_chat(chat)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    # ── Messages ──────────────────────────────────────────────────────────

    def list_messages(
        self,
        user: User,
        chat_id: UUID,
        *,
        limit: int = 40,
        before: str | None = None,
    ) -> MessagePageOut:
        self.get_chat(user, chat_id)
        before_created_at: datetime | None = None
        before_id: UUID | None = None
        if before:
            before_created_at, before_id = self._decode_message_cursor(before)

        messages, has_more = self.repo.list_message_page(
            chat_id,
            limit=limit,
            before_created_at=before_created_at,
            before_id=before_id,
        )
        next_before = (
            self._encode_message_cursor(messages[0]) if has_more and messages else None
        )
        return MessagePageOut(
            items=[self._message_out(m) for m in messages],
            has_more=has_more,
            next_before=next_before,
        )

    def add_message(
        self,
        user: User,
        chat_id: UUID,
        *,
        content: str,
        role: str = "user",
        metadata: dict | None = None,
    ) -> MessageOut:
        from app.core.exceptions import AppError

        body = content.strip()
        if not body:
            raise AppError("Message content is required.", status_code=422)
        if role not in {"user", "assistant", "system"}:
            raise AppError("Invalid message role.", status_code=422)

        chat = self.get_chat(user, chat_id)
        project = self.repo.get_project_for_user(chat.project_id, user.id)
        try:
            msg = self.repo.create_message(
                chat_id=chat.id,
                role=role,
                content=body,
                metadata=metadata,
            )
            self.repo.save_chat(chat)
            if project is not None:
                self.repo.save_project(project)
            self.db.commit()
            self.db.refresh(msg)
            return self._message_out(msg)
        except Exception:
            self.db.rollback()
            raise

    # ── Helpers ───────────────────────────────────────────────────────────

    def _chats_with_previews(self, chats: list[Chat]) -> list[ChatOut]:
        if not chats:
            return []
        previews = self.repo.latest_message_previews([c.id for c in chats])
        result: list[ChatOut] = []
        for chat in chats:
            out = ChatOut.model_validate(chat)
            out.last_message_preview = previews.get(chat.id)
            result.append(out)
        return result

    @staticmethod
    def _message_out(msg: ChatMessage) -> MessageOut:
        return MessageOut(
            id=msg.id,
            chat_id=msg.chat_id,
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
            metadata=msg.metadata_json,
        )

    @staticmethod
    def _encode_message_cursor(message: ChatMessage) -> str:
        raw = f"{message.created_at.isoformat()}|{message.id}".encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_message_cursor(cursor: str) -> tuple[datetime, UUID]:
        try:
            padded = cursor + ("=" * (-len(cursor) % 4))
            raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            created_at_raw, message_id_raw = raw.rsplit("|", 1)
            return datetime.fromisoformat(created_at_raw), UUID(message_id_raw)
        except (binascii.Error, ValueError, UnicodeDecodeError):
            raise AppError("Invalid message cursor.", status_code=422) from None
