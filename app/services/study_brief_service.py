"""Conversational study-brief AI orchestration."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai_prompts.study_brief import STUDY_BRIEF_SYSTEM_PROMPT, STUDY_BRIEF_USER_TEMPLATE
from app.core.exceptions import AppError, NotFoundError
from app.db.models.chat import Chat
from app.db.models.project import Project
from app.db.models.user import User
from app.repositories.project_repository import ProjectRepository
from app.schemas.project import MessageOut
from app.schemas.study_brief import (
    AttachmentBrief,
    BriefPhase,
    StudyBrief,
    StudyBriefUpdate,
    StudyConfirmResponse,
)
from app.services.folder_brief import apply_folder_categories, ensure_default_classification
from app.services.openai_client import chat_json, openai_configured
from app.services.study_brief_validator import (
    apply_defaults,
    compute_missing_fields,
    is_brief_ready_for_create,
    is_brief_ready_for_review,
)
from app.services.study_create_service import create_draft_study_from_brief

logger = logging.getLogger(__name__)


class StudyBriefService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = ProjectRepository(db)

    def get_brief(self, user: User, chat_id: UUID) -> tuple[BriefPhase, StudyBrief]:
        chat, _project = self._owned_chat(user, chat_id)
        brief = self._load_brief(chat)
        phase = self._phase_from_brief(brief)
        return phase, brief

    def update_brief(
        self, user: User, chat_id: UUID, patch: StudyBriefUpdate
    ) -> tuple[BriefPhase, StudyBrief]:
        chat, _project = self._owned_chat(user, chat_id)
        brief = self._load_brief(chat)
        if brief.status == "created":
            raise AppError("This study has already been created and can’t be edited here.", status_code=409)

        data = patch.model_dump(exclude_unset=True)
        merged = brief.model_copy(update=data)
        merged = apply_defaults(merged)
        self._save_brief(chat, merged)
        self.db.commit()
        self.db.refresh(chat)
        return self._phase_from_brief(merged), merged

    def run_ai_turn(
        self,
        user: User,
        chat_id: UUID,
        *,
        content: str,
        attachments: list[AttachmentBrief] | None = None,
    ) -> tuple[MessageOut, MessageOut, BriefPhase, StudyBrief, str | None]:
        chat, project = self._owned_chat(user, chat_id)
        brief = self._load_brief(chat)
        if brief.status == "created":
            raise AppError(
                "Your study is already created. Task generation will be available next.",
                status_code=409,
            )

        attachments = attachments or []
        body = content.strip()
        if not body and not attachments:
            raise AppError("Please enter a message or attach a file.", status_code=422)

        user_meta: dict[str, Any] | None = None
        if attachments:
            brief.merge_attachments(attachments)
            brief = apply_folder_categories(brief, attachments)
            user_meta = {
                "kind": "attachments",
                "attachments": [a.model_dump() for a in attachments],
            }
            if not body:
                cats = sorted({a.category for a in attachments if a.category})
                if cats:
                    body = (
                        f"Uploaded {len(attachments)} image(s) across "
                        f"{len(cats)} categor{'y' if len(cats) == 1 else 'ies'}: "
                        + ", ".join(cats)
                    )
                else:
                    body = f"Uploaded {len(attachments)} file(s): " + ", ".join(
                        a.filename or a.url for a in attachments
                    )

        try:
            user_msg = self.repo.create_message(
                chat_id=chat.id,
                role="user",
                content=body,
                metadata=user_meta,
            )
            self.repo.save_chat(chat)
            self.db.flush()

            history = self.repo.list_recent_messages(chat.id, limit=24)
            ai_payload = self._call_ai(
                project=project,
                brief=brief,
                history=history,
                user_message=body,
                new_attachments=attachments,
            )
            assistant_text = str(ai_payload.get("assistant_message") or "").strip()
            if not assistant_text:
                assistant_text = "Thanks — I’ve updated your study brief. What would you like to refine next?"

            new_brief = self._brief_from_ai(brief, ai_payload)
            new_brief.merge_attachments(brief.attachments)
            # Folder structure from the user wins over AI-invented categories.
            new_brief = apply_folder_categories(
                new_brief, attachments or brief.attachments
            )
            new_brief = ensure_default_classification(new_brief)
            new_brief = apply_defaults(new_brief)
            if is_brief_ready_for_review(new_brief):
                new_brief.status = "ready"
                phase: BriefPhase = "brief_ready"
            else:
                new_brief.status = "gathering"
                phase = "gathering"

            suggested = ai_payload.get("suggested_chat_title")
            suggested_title = (
                str(suggested).strip()[:60]
                if isinstance(suggested, str) and suggested.strip()
                else None
            )

            # Keep message metadata light — full brief lives on chat + response body.
            assistant_meta = {
                "kind": "study_brief",
                "phase": phase,
                "attachment_count": len(new_brief.attachments),
            }
            assistant_msg = self.repo.create_message(
                chat_id=chat.id,
                role="assistant",
                content=assistant_text,
                metadata=assistant_meta,
            )
            self._save_brief(chat, new_brief)
            if suggested_title and (chat.title == "New Chat" or not chat.title.strip()):
                chat.title = suggested_title
                self.repo.save_chat(chat)

            self.db.commit()
            self.db.refresh(user_msg)
            self.db.refresh(assistant_msg)
            self.db.refresh(chat)
            return (
                self._message_out(user_msg),
                self._message_out(assistant_msg),
                phase,
                new_brief,
                suggested_title,
            )
        except AppError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            logger.exception("AI turn failed")
            raise AppError(
                "Something went wrong while talking to the assistant. Please try again.",
                status_code=500,
            ) from None

    def continue_ai_if_needed(
        self, user: User, chat_id: UUID
    ) -> tuple[MessageOut, BriefPhase, StudyBrief, str | None] | None:
        """If the latest message is an unanswered user turn, generate the assistant reply."""
        chat, project = self._owned_chat(user, chat_id)
        brief = self._load_brief(chat)
        if brief.status == "created":
            return None
        history = self.repo.list_recent_messages(chat.id, limit=24)
        if not history or history[-1].role != "user":
            return None

        try:
            ai_payload = self._call_ai(
                project=project,
                brief=brief,
                history=history,
                user_message=history[-1].content,
                new_attachments=[],
            )
            assistant_text = str(ai_payload.get("assistant_message") or "").strip()
            if not assistant_text:
                assistant_text = "Thanks — I’ve updated your study brief. What should we refine next?"

            new_brief = self._brief_from_ai(brief, ai_payload)
            new_brief.merge_attachments(brief.attachments)
            new_brief = apply_folder_categories(new_brief, brief.attachments)
            new_brief = ensure_default_classification(new_brief)
            new_brief = apply_defaults(new_brief)
            if is_brief_ready_for_review(new_brief):
                new_brief.status = "ready"
                phase: BriefPhase = "brief_ready"
            else:
                new_brief.status = "gathering"
                phase = "gathering"

            suggested = ai_payload.get("suggested_chat_title")
            suggested_title = (
                str(suggested).strip()[:60]
                if isinstance(suggested, str) and suggested.strip()
                else None
            )
            assistant_msg = self.repo.create_message(
                chat_id=chat.id,
                role="assistant",
                content=assistant_text,
                metadata={
                    "kind": "study_brief",
                    "phase": phase,
                    "attachment_count": len(new_brief.attachments),
                },
            )
            self._save_brief(chat, new_brief)
            if suggested_title and (chat.title == "New Chat" or not chat.title.strip()):
                chat.title = suggested_title
                self.repo.save_chat(chat)
            self.db.commit()
            self.db.refresh(assistant_msg)
            return self._message_out(assistant_msg), phase, new_brief, suggested_title
        except AppError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            logger.exception("AI continue failed")
            raise AppError(
                "Something went wrong while talking to the assistant. Please try again.",
                status_code=500,
            ) from None

    def confirm_brief(self, user: User, chat_id: UUID) -> StudyConfirmResponse:
        chat, project = self._owned_chat(user, chat_id)
        brief = self._load_brief(chat)
        if brief.study_id and brief.status == "created":
            return StudyConfirmResponse(
                study_id=brief.study_id,
                phase="created",
                study_brief=brief,
                message="Study already created.",
            )

        brief = apply_folder_categories(brief, brief.attachments)
        brief = ensure_default_classification(brief)
        brief = apply_defaults(brief)
        missing = compute_missing_fields(brief, require_grid_images=True)
        if missing:
            raise AppError(
                "Please complete the study brief before continuing. Missing: "
                + ", ".join(missing[:8]),
                status_code=422,
            )

        try:
            study_id = create_draft_study_from_brief(
                self.db,
                creator_id=user.id,
                project_id=project.id,
                brief=brief,
            )
            brief.study_id = study_id
            brief.status = "created"
            brief.missing_fields = []
            self._save_brief(chat, brief)
            assistant_msg = self.repo.create_message(
                chat_id=chat.id,
                role="assistant",
                content=(
                    f"Your study draft **{brief.title}** is ready in MindSurve.\n\n"
                    "Next up we’ll generate tasks (Golden Matrix). You can keep chatting "
                    "if you want to tweak copy before that step."
                ),
                metadata={
                    "kind": "study_created",
                    "study_id": str(study_id),
                    "study_brief": brief.model_dump(mode="json"),
                },
            )
            self.repo.save_chat(chat)
            self.db.commit()
            self.db.refresh(assistant_msg)
            return StudyConfirmResponse(
                study_id=study_id,
                phase="created",
                study_brief=brief,
                message="Study draft created successfully.",
            )
        except AppError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            logger.exception("Confirm study brief failed")
            raise AppError(
                "We couldn’t create your study. Please try again.",
                status_code=500,
            ) from None

    # ── internals ─────────────────────────────────────────────────────────

    def _owned_chat(self, user: User, chat_id: UUID) -> tuple[Chat, Project]:
        chat = self.repo.get_chat_for_user(chat_id, user.id)
        if chat is None:
            raise NotFoundError("Chat not found.")
        project = self.repo.get_project_for_user(chat.project_id, user.id)
        if project is None:
            raise NotFoundError("Project not found.")
        return chat, project

    def _load_brief(self, chat: Chat) -> StudyBrief:
        raw = getattr(chat, "study_brief", None) or {}
        if not isinstance(raw, dict):
            raw = {}
        try:
            return apply_defaults(StudyBrief.model_validate(raw))
        except Exception:
            return apply_defaults(StudyBrief())

    def _save_brief(self, chat: Chat, brief: StudyBrief) -> None:
        chat.study_brief = brief.model_dump(mode="json")
        self.repo.save_chat(chat)

    @staticmethod
    def _phase_from_brief(brief: StudyBrief) -> BriefPhase:
        if brief.status == "created" or brief.study_id:
            return "created"
        if brief.status == "ready" or is_brief_ready_for_review(brief):
            return "brief_ready"
        return "gathering"

    @staticmethod
    def _compact_attachment_for_ai(att: AttachmentBrief | dict[str, Any]) -> dict[str, Any]:
        """Omit long Azure SAS URLs from the model prompt (they bloat tokens / latency)."""
        if isinstance(att, AttachmentBrief):
            return {
                "filename": att.filename or "",
                "content_type": att.content_type or "",
                "category": att.category,
                "relative_path": att.relative_path,
                "has_url": bool(att.url),
            }
        return {
            "filename": str(att.get("filename") or ""),
            "content_type": str(att.get("content_type") or ""),
            "category": att.get("category"),
            "relative_path": att.get("relative_path"),
            "has_url": bool(att.get("url")),
        }

    def _compact_brief_for_ai(self, brief: StudyBrief) -> dict[str, Any]:
        data = brief.model_dump(mode="json")
        data["attachments"] = [
            self._compact_attachment_for_ai(a) for a in brief.attachments
        ]
        return data

    def _call_ai(
        self,
        *,
        project: Project,
        brief: StudyBrief,
        history: list[Any],
        user_message: str,
        new_attachments: list[AttachmentBrief],
    ) -> dict[str, Any]:
        if not openai_configured():
            return self._heuristic_ai(brief, user_message, new_attachments)

        transcript_lines: list[str] = []
        for msg in history[-16:]:
            role = getattr(msg, "role", "user")
            content = str(getattr(msg, "content", "") or "")
            if len(content) > 800:
                content = content[:800] + "…"
            transcript_lines.append(f"{role.upper()}: {content}")

        user_prompt = STUDY_BRIEF_USER_TEMPLATE.format(
            project_name=project.name,
            project_id=str(project.id),
            current_brief_json=json.dumps(
                self._compact_brief_for_ai(brief), ensure_ascii=False, indent=2
            ),
            conversation_transcript="\n".join(transcript_lines) or "(empty)",
            user_message=user_message,
            new_attachments_json=json.dumps(
                [self._compact_attachment_for_ai(a) for a in new_attachments],
                ensure_ascii=False,
            ),
        )
        return chat_json(system_prompt=STUDY_BRIEF_SYSTEM_PROMPT, user_prompt=user_prompt)

    def _heuristic_ai(
        self,
        brief: StudyBrief,
        user_message: str,
        new_attachments: list[AttachmentBrief],
    ) -> dict[str, Any]:
        """Deterministic fallback when OPENAI_API_KEY is missing (tests / local)."""
        text = user_message.strip()
        lower = text.lower()
        if new_attachments:
            brief.merge_attachments(new_attachments)
            for att in new_attachments:
                ctype = (att.content_type or "").lower()
                if ctype.startswith("image/") and brief.study_type is None:
                    brief.study_type = "grid"

        if "text study" in lower or "text statements" in lower:
            brief.study_type = "text"
        elif "grid" in lower or "logo" in lower or "image" in lower:
            brief.study_type = "grid"

        generic_openers = {
            "let's build the first study",
            "lets build the first study",
            "let's build a study",
            "lets build a study",
            "create a study",
            "start a study",
        }
        if (
            not brief.title
            and len(text.split()) >= 4
            and lower.rstrip(".!") not in generic_openers
        ):
            # crude title from first sentence
            brief.title = text.split(".")[0].strip()[:200]

        if "pet shedding" in lower and not brief.background:
            brief.background = (
                "Research to identify which logos and visual identities best resonate "
                "with pet owners concerned about pet shedding."
            )[:2000]
            brief.study_type = brief.study_type or "grid"
            brief.main_question = "How suitable is this logo for a pet-shedding brand?"
            brief.orientation_text = (
                "You will see several logo options. Rate how suitable each feels "
                "for a brand focused on pet shedding solutions."
            )
            if len(brief.categories) < 3:
                brief.categories = []
                for cname, elems in [
                    ("Shape", ["Circle mark", "Shield mark", "Wordmark"]),
                    ("Color", ["Blue calm", "Green fresh", "Warm coral"]),
                    ("Symbol", ["Pet silhouette", "Fur swirl", "Home + pet"]),
                ]:
                    from app.schemas.study_brief import CategoryBrief, ElementBrief

                    brief.categories.append(
                        CategoryBrief(
                            name=cname,
                            elements=[
                                ElementBrief(
                                    name=e,
                                    element_type="image" if brief.study_type == "grid" else "text",
                                    content="" if brief.study_type == "grid" else e,
                                )
                                for e in elems
                            ],
                        )
                    )

        brief = apply_defaults(brief)
        ready = is_brief_ready_for_review(brief)
        create_ready = is_brief_ready_for_create(brief)
        if ready and not create_ready and brief.study_type == "grid":
            msg = (
                "I’ve drafted your study brief. Review the summary card — for a grid study, "
                "upload an image for each element (or ask me to switch to a text study), "
                "then press Continue when you’re ready."
            )
        elif ready:
            msg = (
                "Your study brief looks complete. Review the summary and press "
                "Continue with study when you’re happy."
            )
        else:
            msg = (
                "Absolutely — **what are you trying to learn or test?**\n\n"
                "Tell me a little about the product, idea, or decision behind the study, "
                "and I’ll shape the research approach for you."
            )
        return {
            "assistant_message": msg,
            "phase": "brief_ready" if ready else "gathering",
            "suggested_chat_title": brief.title[:60] if brief.title else None,
            "missing_fields": brief.missing_fields,
            "study_brief": brief.model_dump(mode="json"),
        }

    def _brief_from_ai(self, current: StudyBrief, payload: dict[str, Any]) -> StudyBrief:
        raw = payload.get("study_brief")
        if not isinstance(raw, dict):
            return current
        try:
            incoming = StudyBrief.model_validate(raw)
        except Exception:
            logger.warning("AI study_brief failed validation; keeping prior brief")
            return current
        # Preserve study_id / created status
        if current.study_id:
            incoming.study_id = current.study_id
        if current.status == "created":
            incoming.status = "created"
        if not incoming.attachments and current.attachments:
            incoming.attachments = current.attachments
        return incoming

    @staticmethod
    def _message_out(msg: Any) -> MessageOut:
        return MessageOut(
            id=msg.id,
            chat_id=msg.chat_id,
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
            metadata=msg.metadata_json,
        )
