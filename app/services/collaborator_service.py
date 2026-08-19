"""Collaborator invite / list / remove for shared projects."""

from __future__ import annotations

import logging
import re
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, NotFoundError
from app.db.models.membership import ProjectMember, StudyMember
from app.db.models.project import Project
from app.db.models.user import User
from app.repositories.project_repository import ProjectRepository
from app.services.collaborator_email import send_project_invite_email
from app.services.project_service import is_inbox_project

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return email.strip().lower()


class CollaboratorService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.projects = ProjectRepository(db)

    def claim_pending_invites(self, user: User) -> int:
        """Link pending project/study invites to this user by email."""
        email = normalize_email(user.email or "")
        if not email:
            return 0
        from sqlalchemy import func

        proj = self.db.execute(
            update(ProjectMember)
            .where(
                func.lower(ProjectMember.invited_email) == email,
                ProjectMember.user_id.is_(None),
            )
            .values(user_id=user.id)
        )
        study = self.db.execute(
            update(StudyMember)
            .where(
                func.lower(StudyMember.invited_email) == email,
                StudyMember.user_id.is_(None),
            )
            .values(user_id=user.id)
        )
        linked = (proj.rowcount or 0) + (study.rowcount or 0)
        if linked:
            logger.info("Linked %s pending invite row(s) for %s", linked, email)
        return linked

    def invite(
        self,
        actor: User,
        project_id: UUID,
        *,
        email: str,
    ) -> ProjectMember:
        project = self.projects.get_project_for_user(project_id, actor.id)
        if project is None:
            raise NotFoundError("Project not found.")
        if is_inbox_project(project):
            raise AppError(
                "Move this chat into a project before inviting collaborators.",
                status_code=422,
            )
        if not self._can_invite(project, actor):
            raise AppError(
                "You don’t have permission to invite collaborators.",
                status_code=403,
            )

        invited = normalize_email(email)
        if not invited or not _EMAIL_RE.match(invited):
            raise AppError("Please enter a valid email address.", status_code=422)
        if invited == normalize_email(actor.email or ""):
            raise AppError("You can’t invite yourself.", status_code=422)

        existing = self.db.scalars(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.invited_email == invited,
            )
        ).first()
        if existing is not None:
            raise AppError(
                "That person is already a collaborator on this project.",
                status_code=409,
            )

        owner = self.db.get(User, project.creator_id)
        if owner and normalize_email(owner.email or "") == invited:
            raise AppError(
                "That person already owns this project.",
                status_code=409,
            )

        invitee = self.db.scalars(
            select(User).where(User.email == invited)
        ).first()
        if invitee is None:
            # Legacy accounts may store mixed-case emails
            invitee = self.db.scalars(
                select(User).where(User.email.ilike(invited))
            ).first()

        member = ProjectMember(
            id=uuid4(),
            project_id=project_id,
            user_id=invitee.id if invitee else None,
            role="editor",
            invited_email=invited,
        )
        self.db.add(member)
        self.db.flush()
        # Backfill studies created in project chats with null project_id, then sync.
        self.repair_project_study_links(project_id)
        self._sync_member_to_all_studies(project_id, member)
        self.db.commit()
        self.db.refresh(member)

        inviter_name = (actor.name or actor.email or "A teammate").strip()
        try:
            send_project_invite_email(
                to_email=invited,
                inviter_name=inviter_name,
                project_name=project.name or "Untitled project",
                is_new_user=invitee is None,
            )
        except Exception:
            logger.exception("Invite email failed for %s", invited)

        return member

    def invite_for_chat(
        self,
        actor: User,
        chat_id: UUID,
        *,
        email: str,
    ) -> tuple[ProjectMember, Project, bool]:
        """Invite a collaborator to the chat's project.

        Inbox chats are promoted into a new named project first (so Unilever /
        study sharing has a real project home), then the invite runs as usual.
        """
        chat = self.projects.get_chat_for_user(chat_id, actor.id)
        if chat is None:
            raise NotFoundError("Chat not found.")

        project = self.db.get(Project, chat.project_id)
        if project is None:
            raise NotFoundError("Project not found.")

        # Validate email before promoting so we don't create empty projects on bad input.
        invited = normalize_email(email)
        if not invited or not _EMAIL_RE.match(invited):
            raise AppError("Please enter a valid email address.", status_code=422)
        if invited == normalize_email(actor.email or ""):
            raise AppError("You can’t invite yourself.", status_code=422)

        promoted = False
        if is_inbox_project(project):
            if project.creator_id != actor.id:
                raise AppError(
                    "Only the chat owner can share a personal chat.",
                    status_code=403,
                )
            project = self._promote_inbox_chat_to_project(actor, chat)
            promoted = True

        member = self.invite(actor, project.id, email=email)
        return member, project, promoted

    def _promote_inbox_chat_to_project(self, actor: User, chat: object) -> Project:
        """Move an inbox chat into a new beginner project named from the chat/brief."""
        from app.db.models.chat import Chat

        if not isinstance(chat, Chat):
            raise AppError("Invalid chat.", status_code=500)

        title = (chat.title or "").strip()
        brief = getattr(chat, "study_brief", None) or {}
        if isinstance(brief, dict):
            brief_title = (brief.get("title") or "").strip()
            if brief_title:
                title = brief_title
        if not title or title.lower() in {"new chat", "untitled"}:
            title = "Shared chat"
        title = title[:255]

        project = self.projects.create_project(
            user_id=actor.id,
            title=title,
            workflow_type="beginner",
            description="Created when sharing a personal chat",
        )
        chat.project_id = project.id
        self.projects.save_chat(chat)
        self.projects.save_project(project)
        self.ensure_chat_studies_linked(chat, project)
        self.db.flush()
        logger.info(
            "Promoted inbox chat %s to project %s (%s)",
            chat.id,
            project.id,
            title,
        )
        return project

    def list_collaborators(
        self, actor: User, project_id: UUID
    ) -> list[dict[str, object]]:
        project = self.projects.get_project_for_user(project_id, actor.id)
        if project is None:
            raise NotFoundError("Project not found.")

        owner = self.db.get(User, project.creator_id)
        rows: list[dict[str, object]] = [
            {
                "id": str(project.creator_id),
                "email": (owner.email if owner else "") or "",
                "name": (owner.name if owner else None),
                "is_owner": True,
                "status": "active",
            }
        ]

        members = self.db.scalars(
            select(ProjectMember)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.created_at.asc())
        ).all()
        for m in members:
            if m.user_id == project.creator_id:
                continue
            user = self.db.get(User, m.user_id) if m.user_id else None
            rows.append(
                {
                    "id": str(m.id),
                    "email": m.invited_email,
                    "name": user.name if user else None,
                    "is_owner": False,
                    "status": "active" if m.user_id else "pending",
                }
            )
        return rows

    def remove(
        self,
        actor: User,
        project_id: UUID,
        member_id: UUID,
    ) -> None:
        project = self.projects.get_project_for_user(project_id, actor.id)
        if project is None:
            raise NotFoundError("Project not found.")
        if project.creator_id != actor.id:
            raise AppError(
                "Only the project owner can remove collaborators.",
                status_code=403,
            )

        member = self.db.get(ProjectMember, member_id)
        if member is None or member.project_id != project_id:
            raise NotFoundError("Collaborator not found.")

        study_ids = [
            row[0]
            for row in self.db.execute(
                text("SELECT id FROM studies WHERE project_id = :pid"),
                {"pid": project_id},
            ).all()
        ]
        if study_ids:
            from sqlalchemy import delete as sa_delete

            self.db.execute(
                sa_delete(StudyMember).where(
                    StudyMember.invited_email == member.invited_email,
                    StudyMember.study_id.in_(study_ids),
                )
            )

        self.db.delete(member)
        self.db.commit()

    def sync_new_study_to_project_members(
        self,
        *,
        study_id: UUID,
        project_id: UUID,
    ) -> None:
        """When a study is created, grant all project collaborators access."""
        project = self.db.get(Project, project_id)
        if project is None:
            return

        owner = self.db.get(User, project.creator_id)
        owner_email = normalize_email((owner.email if owner else "") or "")
        if owner_email:
            self._upsert_study_member(
                study_id=study_id,
                user_id=project.creator_id,
                role="admin",
                invited_email=owner_email,
            )

        members = self.db.scalars(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        ).all()
        for pm in members:
            study_role = "editor" if pm.role == "editor" else "viewer"
            self._upsert_study_member(
                study_id=study_id,
                user_id=pm.user_id,
                role=study_role,
                invited_email=pm.invited_email,
            )

    def _upsert_study_member(
        self,
        *,
        study_id: UUID,
        user_id: UUID | None,
        role: str,
        invited_email: str,
    ) -> None:
        """Insert study_members via SQL to avoid ORM dependency on studies table."""
        email = normalize_email(invited_email)
        if not email:
            return
        existing = self.db.scalars(
            select(StudyMember).where(
                StudyMember.study_id == study_id,
                StudyMember.invited_email == email,
            )
        ).first()
        if existing is not None:
            if user_id and existing.user_id is None:
                existing.user_id = user_id
            return
        self.db.execute(
            text(
                """
                INSERT INTO study_members (id, study_id, user_id, role, invited_email)
                VALUES (
                    :id, :study_id, :user_id,
                    CAST(:role AS study_role_enum),
                    :email
                )
                ON CONFLICT ON CONSTRAINT uq_study_members_study_email DO NOTHING
                """
            ),
            {
                "id": uuid4(),
                "study_id": study_id,
                "user_id": user_id,
                "role": role,
                "email": email,
            },
        )

    def ensure_study_linked_to_project(
        self,
        *,
        study_id: UUID,
        project_id: UUID,
    ) -> bool:
        """Set studies.project_id onto a named project and sync collaborators.

        Reassigns when project_id is null OR currently pointing at an inbox
        ("Personal") project — those are not real Unilever project homes.
        Refuses to steal a study already on a different named project.
        """
        row = self.db.execute(
            text("SELECT project_id FROM studies WHERE id = :id"),
            {"id": study_id},
        ).first()
        if row is None:
            logger.warning("ensure_study_linked: study %s not found", study_id)
            return False

        current = row[0]
        if current == project_id:
            self.sync_new_study_to_project_members(
                study_id=study_id,
                project_id=project_id,
            )
            return True

        if current is not None:
            current_project = self.db.get(Project, current)
            # Only auto-move off inbox/Personal (or missing project rows).
            if current_project is not None and not is_inbox_project(current_project):
                logger.info(
                    "Study %s already on project %s; not moving to %s",
                    study_id,
                    current,
                    project_id,
                )
                return False

        self.db.execute(
            text(
                """
                UPDATE studies
                SET project_id = :pid
                WHERE id = :id
                """
            ),
            {"pid": project_id, "id": study_id},
        )
        logger.info(
            "Linked study %s to project %s (was %s)",
            study_id,
            project_id,
            current,
        )

        self.sync_new_study_to_project_members(
            study_id=study_id,
            project_id=project_id,
        )
        return True

    def ensure_chat_studies_linked(self, chat: object, project: Project) -> int:
        """Attach any studies referenced by this chat to a named project."""
        if is_inbox_project(project):
            return 0

        study_ids: set[UUID] = set()
        brief = getattr(chat, "study_brief", None) or {}
        if isinstance(brief, dict):
            raw = brief.get("study_id")
            if raw:
                try:
                    study_ids.add(UUID(str(raw)))
                except (TypeError, ValueError):
                    pass

        try:
            from app.db.models.study_generation_run import StudyGenerationRun

            chat_id = getattr(chat, "id", None)
            if chat_id is not None:
                for sid in self.db.scalars(
                    select(StudyGenerationRun.study_id).where(
                        StudyGenerationRun.chat_id == chat_id
                    )
                ).all():
                    if sid:
                        study_ids.add(sid)
        except Exception:
            logger.exception(
                "Failed reading generation runs for chat study link repair"
            )

        linked = 0
        for sid in study_ids:
            if self.ensure_study_linked_to_project(
                study_id=sid, project_id=project.id
            ):
                linked += 1
        return linked

    def repair_project_study_links(self, project_id: UUID) -> int:
        """Backfill project_id for studies created from chats in this project."""
        project = self.db.get(Project, project_id)
        if project is None or is_inbox_project(project):
            return 0
        chats = self.projects.list_chats_for_project(project_id)
        ensured = 0
        for chat in chats:
            ensured += self.ensure_chat_studies_linked(chat, project)
        if ensured:
            logger.info(
                "Ensured %s study↔project link(s) for project %s",
                ensured,
                project_id,
            )
        return ensured

    def _can_invite(self, project: Project, actor: User) -> bool:
        if project.creator_id == actor.id:
            return True
        member = self.db.scalars(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == actor.id,
                ProjectMember.role == "editor",
            )
        ).first()
        return member is not None

    def _sync_member_to_all_studies(
        self, project_id: UUID, project_member: ProjectMember
    ) -> None:
        study_ids = [
            row[0]
            for row in self.db.execute(
                text("SELECT id FROM studies WHERE project_id = :pid"),
                {"pid": project_id},
            ).all()
        ]
        study_role = "editor" if project_member.role == "editor" else "viewer"
        for study_id in study_ids:
            self._upsert_study_member(
                study_id=study_id,
                user_id=project_member.user_id,
                role=study_role,
                invited_email=project_member.invited_email,
            )
        logger.info(
            "Synced collaborator %s to %s studies",
            project_member.invited_email,
            len(study_ids),
        )
