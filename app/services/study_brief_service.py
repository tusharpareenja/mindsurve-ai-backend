"""Conversational study-brief AI orchestration."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai_prompts.study_brief import STUDY_BRIEF_SYSTEM_PROMPT, STUDY_BRIEF_USER_TEMPLATE
from app.core.config import get_settings
from app.core.exceptions import AppError, NotFoundError
from app.db.models.chat import Chat
from app.db.models.project import Project
from app.db.models.user import User
from app.repositories.project_repository import ProjectRepository
from app.repositories.study_generation_repository import StudyGenerationRepository
from app.repositories.synthetic_collection_repository import (
    SyntheticCollectionRepository,
)
from app.schemas.project import MessageOut
from app.schemas.study_brief import (
    AttachmentBrief,
    BriefPhase,
    StudyBrief,
    StudyBriefUpdate,
    StudyConfirmResponse,
)
from app.services.audience_infer import (
    apply_inferred_audience,
    apply_text_study_hint,
)
from app.services.folder_brief import (
    apply_folder_categories,
    ensure_default_classification,
)
from app.services.openai_client import chat_json, openai_configured
from app.services.study_brief_validator import (
    apply_defaults,
    compute_missing_fields,
    is_brief_ready_for_create,
    is_brief_ready_for_review,
)
from app.services.study_create_service import create_draft_study_from_brief
from app.services.text_brief import ensure_text_study_structure

logger = logging.getLogger(__name__)


class StudyBriefService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = ProjectRepository(db)
        self.generation_repo = StudyGenerationRepository(db)
        self.collection_repo = SyntheticCollectionRepository(db)

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
        # Merge as plain dicts then re-validate so nested models (e.g. audience)
        # are proper pydantic instances rather than raw dicts.
        base = brief.model_dump(mode="json")
        base.update(data)
        try:
            merged = StudyBrief.model_validate(base)
        except Exception:
            raise AppError(
                "Those changes weren’t valid. Please review and try again.",
                status_code=422,
            ) from None
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

        attachments = attachments or []
        body = content.strip()
        if not body and not attachments:
            raise AppError("Please enter a message or attach a file.", status_code=422)

        # After launch, allow chat but freeze the study brief (no structural edits).
        if brief.status == "created":
            return self._run_post_launch_chat(
                chat=chat,
                project=project,
                brief=brief,
                body=body,
                attachments=attachments,
            )

        user_meta: dict[str, Any] | None = None
        if attachments:
            brief.merge_attachments(attachments)
            brief = apply_folder_categories(brief, attachments)
            user_meta = {
                "kind": "attachments",
                "attachments": [
                    a.model_dump(exclude={"extracted_text"}) for a in attachments
                ],
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
            corpus = self._user_text_corpus(history, body)
            brief = apply_inferred_audience(brief, text=corpus)
            brief = apply_text_study_hint(
                brief,
                text=corpus,
                has_images=any(
                    (a.content_type or "").startswith("image/") for a in attachments
                ),
            )
            ai_payload = self._call_ai(
                project=project,
                chat=chat,
                brief=brief,
                history=history,
                user_message=body,
                new_attachments=attachments,
            )
            assistant_text = str(ai_payload.get("assistant_message") or "").strip()
            intent = self._resolve_intent(ai_payload, has_attachments=bool(attachments))

            phase: BriefPhase
            if intent == "answer":
                # Informational turn — never mutate this chat's brief or show Continue.
                if not assistant_text:
                    assistant_text = "Here’s what I found. What would you like to do next?"
                new_brief = brief
                phase = self._phase_from_brief(brief)
            else:
                if not assistant_text:
                    assistant_text = "Thanks — I’ve updated your study brief. What would you like to refine next?"
                new_brief = self._brief_from_ai(brief, ai_payload)
                new_brief.merge_attachments(brief.attachments)
                # Folder structure from the user wins over AI-invented categories.
                new_brief = apply_folder_categories(
                    new_brief, attachments or brief.attachments
                )
                new_brief = apply_inferred_audience(new_brief, text=corpus)
                new_brief = apply_text_study_hint(
                    new_brief,
                    text=corpus,
                    has_images=any(
                        (a.content_type or "").startswith("image/")
                        for a in (attachments or [])
                    ),
                )
                new_brief = ensure_text_study_structure(new_brief)
                new_brief = ensure_default_classification(new_brief)
                new_brief = apply_defaults(new_brief)
                if is_brief_ready_for_review(new_brief):
                    new_brief.status = "ready"
                    phase = "brief_ready"
                else:
                    new_brief.status = "gathering"
                    phase = "gathering"
                assistant_text = self._align_assistant_text(
                    assistant_text, phase=phase, brief=new_brief
                )

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
            if intent != "answer":
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

    def _run_post_launch_chat(
        self,
        *,
        chat: Any,
        project: Any,
        brief: StudyBrief,
        body: str,
        attachments: list[AttachmentBrief],
    ) -> tuple[MessageOut, MessageOut, BriefPhase, StudyBrief, str | None]:
        """Conversational replies after the study is live — brief structure stays frozen."""
        if not body.strip() and attachments:
            body = (
                f"Uploaded {len(attachments)} file(s) while the study is live "
                "(attachments are noted but the study brief can’t change now)."
            )
        try:
            user_msg = self.repo.create_message(
                chat_id=chat.id,
                role="user",
                content=body,
                metadata=(
                    {
                        "kind": "attachments",
                        "attachments": [
                            a.model_dump(exclude={"extracted_text"})
                            for a in attachments
                        ],
                    }
                    if attachments
                    else {"kind": "post_launch_chat"}
                ),
            )
            self.repo.save_chat(chat)
            self.db.flush()

            history = self.repo.list_recent_messages(chat.id, limit=24)
            locked_user_message = (
                f"{body}\n\n"
                "[SYSTEM NOTE: This study is already created and collecting responses. "
                "Do NOT change any study_brief fields. Reply helpfully about progress, "
                "results, collection, or next steps. Keep study_brief identical to current.]"
            )
            ai_payload = self._call_ai(
                project=project,
                chat=chat,
                brief=brief,
                history=history,
                user_message=locked_user_message,
                new_attachments=[],
            )
            assistant_text = str(ai_payload.get("assistant_message") or "").strip()
            if not assistant_text:
                assistant_text = (
                    "Your study is live and collecting responses. "
                    "Ask me anything about progress or what comes next — "
                    "including other studies in this project."
                )
            assistant_msg = self.repo.create_message(
                chat_id=chat.id,
                role="assistant",
                content=assistant_text,
                metadata={"kind": "post_launch_chat", "phase": "created"},
            )
            self.db.commit()
            self.db.refresh(user_msg)
            self.db.refresh(assistant_msg)
            self.db.refresh(chat)
            return (
                self._message_out(user_msg),
                self._message_out(assistant_msg),
                "created",
                brief,
                None,
            )
        except AppError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            logger.exception("Post-launch AI turn failed")
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
        history = self.repo.list_recent_messages(chat.id, limit=24)
        if not history or history[-1].role != "user":
            return None

        # Post-launch: finish an unanswered user turn without mutating the brief.
        if brief.status == "created":
            try:
                locked_user_message = (
                    f"{history[-1].content}\n\n"
                    "[SYSTEM NOTE: This study is already created and collecting responses. "
                    "Do NOT change any study_brief fields. Reply helpfully about progress, "
                    "results, collection, or next steps.]"
                )
                ai_payload = self._call_ai(
                    project=project,
                    chat=chat,
                    brief=brief,
                    history=history,
                    user_message=locked_user_message,
                    new_attachments=[],
                )
                assistant_text = str(ai_payload.get("assistant_message") or "").strip()
                if not assistant_text:
                    assistant_text = (
                        "Your study is live and collecting responses. "
                        "Ask me anything about progress or what comes next — "
                        "including other studies in this project."
                    )
                assistant_msg = self.repo.create_message(
                    chat_id=chat.id,
                    role="assistant",
                    content=assistant_text,
                    metadata={"kind": "post_launch_chat", "phase": "created"},
                )
                self.db.commit()
                self.db.refresh(assistant_msg)
                return (
                    self._message_out(assistant_msg),
                    "created",
                    brief,
                    None,
                )
            except Exception:
                self.db.rollback()
                logger.exception("Post-launch continue failed")
                return None

        try:
            corpus = self._user_text_corpus(history, history[-1].content)
            brief = apply_inferred_audience(brief, text=corpus)
            brief = apply_text_study_hint(brief, text=corpus, has_images=False)
            ai_payload = self._call_ai(
                project=project,
                chat=chat,
                brief=brief,
                history=history,
                user_message=history[-1].content,
                new_attachments=[],
            )
            assistant_text = str(ai_payload.get("assistant_message") or "").strip()
            intent = self._resolve_intent(ai_payload, has_attachments=False)

            phase: BriefPhase
            if intent == "answer":
                if not assistant_text:
                    assistant_text = "Here’s what I found. What would you like to do next?"
                new_brief = brief
                phase = self._phase_from_brief(brief)
            else:
                if not assistant_text:
                    assistant_text = "Thanks — I’ve updated your study brief. What should we refine next?"
                new_brief = self._brief_from_ai(brief, ai_payload)
                new_brief.merge_attachments(brief.attachments)
                new_brief = apply_folder_categories(new_brief, brief.attachments)
                new_brief = apply_inferred_audience(new_brief, text=corpus)
                new_brief = apply_text_study_hint(
                    new_brief, text=corpus, has_images=False
                )
                new_brief = ensure_text_study_structure(new_brief)
                new_brief = ensure_default_classification(new_brief)
                new_brief = apply_defaults(new_brief)
                if is_brief_ready_for_review(new_brief):
                    new_brief.status = "ready"
                    phase = "brief_ready"
                else:
                    new_brief.status = "gathering"
                    phase = "gathering"
                assistant_text = self._align_assistant_text(
                    assistant_text, phase=phase, brief=new_brief
                )

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
            if intent != "answer":
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
        for att in brief.attachments:
            att.extracted_text = None
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
    def _user_text_corpus(history: list[Any], extra: str = "") -> str:
        parts: list[str] = []
        for msg in history:
            if getattr(msg, "role", "") == "user":
                parts.append(str(getattr(msg, "content", "") or ""))
        if extra and extra not in parts:
            parts.append(extra)
        return "\n".join(p for p in parts if p.strip())

    @staticmethod
    def _resolve_intent(payload: dict[str, Any], *, has_attachments: bool) -> str:
        """Normalize the AI's turn intent.

        - "answer": informational reply, brief must not change.
        - "copy_sibling": copy a sibling brief into this chat.
        - "build": create/refine this chat's own brief (default).
        Uploading files always implies building, regardless of what the model says.
        """
        if has_attachments:
            return "build"
        raw = str(payload.get("intent") or "").strip().lower()
        if raw in {"answer", "copy_sibling", "build"}:
            return raw
        return "build"

    @staticmethod
    def _compact_attachment_for_ai(att: AttachmentBrief | dict[str, Any]) -> dict[str, Any]:
        """Omit long Azure SAS URLs from the model prompt (they bloat tokens / latency)."""
        if isinstance(att, AttachmentBrief):
            excerpt = (att.extracted_text or "").strip()
            return {
                "filename": att.filename or "",
                "content_type": att.content_type or "",
                "category": att.category,
                "relative_path": att.relative_path,
                "has_url": bool(att.url),
                "is_document": bool(excerpt) or (
                    (att.content_type or "").lower()
                    in {
                        "application/pdf",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "text/plain",
                    }
                ),
                "extracted_chars": len(excerpt) if excerpt else 0,
            }
        excerpt = str(att.get("extracted_text") or "").strip()
        return {
            "filename": str(att.get("filename") or ""),
            "content_type": str(att.get("content_type") or ""),
            "category": att.get("category"),
            "relative_path": att.get("relative_path"),
            "has_url": bool(att.get("url")),
            "is_document": bool(excerpt),
            "extracted_chars": len(excerpt) if excerpt else 0,
        }

    @staticmethod
    def _document_excerpts_for_ai(attachments: list[AttachmentBrief]) -> str:
        """Concatenated extracted PDF/Word text for the current turn."""
        chunks: list[str] = []
        budget = 24_000
        used = 0
        for att in attachments:
            text = (att.extracted_text or "").strip()
            if not text:
                continue
            name = att.filename or "document"
            header = f"### {name}\n"
            take = min(len(text), max(0, budget - used))
            if take <= 0:
                break
            body = text[:take]
            if take < len(text):
                body += "\n… [truncated]"
            chunks.append(header + body)
            used += take
        return "\n\n".join(chunks) if chunks else "(none this turn)"

    def _compact_brief_for_ai(self, brief: StudyBrief) -> dict[str, Any]:
        data = brief.model_dump(mode="json")
        data["attachments"] = [
            self._compact_attachment_for_ai(a) for a in brief.attachments
        ]
        return data

    @staticmethod
    def _preview_url(study_id: UUID | str) -> str:
        settings = get_settings()
        base = (settings.STUDY_PREVIEW_BASE_URL or "").rstrip("/")
        return f"{base}?studyId={study_id}"

    @staticmethod
    def _share_url(study_id: UUID | str) -> str:
        settings = get_settings()
        base = (settings.STUDY_SHARE_BASE_URL or "https://mindsurve.com").rstrip("/")
        return f"{base}/participate/{study_id}"

    def _sibling_generation_context(self, chat_id: UUID) -> dict[str, Any] | None:
        """Task-generation + launch state for a sibling chat (URLs, status)."""
        try:
            run = self.generation_repo.latest_for_chat(chat_id)
        except Exception:
            return None
        if run is None:
            return None
        study_id = run.study_id
        preview = run.preview_url or (self._preview_url(study_id) if study_id else None)
        share = run.share_url or (
            self._share_url(study_id)
            if study_id and str(run.study_status) == "live"
            else None
        )
        return {
            "status": run.status,
            "study_status": run.study_status,
            "study_id": str(study_id) if study_id else None,
            "preview_url": preview,
            "share_url": share,
            "launched": str(run.study_status) == "live",
        }

    def _sibling_collection_context(self, chat_id: UUID) -> dict[str, Any] | None:
        """Synthetic respondent collection state for a sibling chat."""
        try:
            run = self.collection_repo.latest_for_chat(chat_id)
        except Exception:
            return None
        if run is None:
            return None
        stats = run.stats_json if isinstance(run.stats_json, dict) else {}
        return {
            "mode": run.mode,
            "status": run.status,
            "progress": round(float(run.progress or 0), 1),
            "message": run.message or "",
            "respondents_requested": run.respondents_requested,
            "respondents_completed": run.respondents_completed,
            "total_responses": stats.get("total"),
            "in_progress": stats.get("in_progress"),
            "completed": stats.get("completed"),
            "abandoned": stats.get("abandoned"),
            "completion_rate": stats.get("completion_rate"),
            "is_active": run.status in {"queued", "running"},
            "is_completed": run.status == "completed",
        }

    def _sibling_study_summaries(
        self, project_id: UUID, current_chat_id: UUID
    ) -> list[dict[str, Any]]:
        """Compact briefs from other chats in the same project for shared AI context."""
        siblings = self.repo.list_chats_for_project(project_id)
        out: list[dict[str, Any]] = []
        for sibling in siblings:
            if sibling.id == current_chat_id:
                continue
            try:
                brief = self._load_brief(sibling)
            except Exception:
                continue
            cats = [
                {
                    "name": c.name,
                    "element_count": len(c.elements),
                    "element_names": [e.name for e in c.elements[:8]],
                }
                for c in brief.categories[:8]
            ]
            generation = self._sibling_generation_context(sibling.id)
            collection = self._sibling_collection_context(sibling.id)
            study_id = (
                str(brief.study_id)
                if brief.study_id
                else (generation or {}).get("study_id")
            )
            preview_url = (generation or {}).get("preview_url")
            share_url = (generation or {}).get("share_url")
            if not preview_url and study_id:
                preview_url = self._preview_url(study_id)
            out.append(
                {
                    "chat_id": str(sibling.id),
                    "chat_title": sibling.title or "Untitled chat",
                    "brief_status": brief.status,
                    "study_id": study_id,
                    "title": brief.title,
                    "background": (brief.background or "")[:400],
                    "study_type": brief.study_type,
                    "main_question": brief.main_question,
                    "orientation_text": (brief.orientation_text or "")[:300],
                    "categories": cats,
                    "element_total": sum(len(c.elements) for c in brief.categories),
                    "classification_questions": [
                        {
                            "question_text": q.question_text,
                            "options": q.options,
                        }
                        for q in brief.classification_questions[:12]
                    ],
                    "audience": brief.audience.model_dump(mode="json"),
                    "attachment_count": len(brief.attachments),
                    "preview_url": preview_url,
                    "share_url": share_url,
                    "generation": generation,
                    "collection": collection,
                    "full_brief": self._compact_brief_for_ai(brief),
                }
            )
        return out

    @staticmethod
    def _is_initial_greeting(message: str, brief: StudyBrief) -> bool:
        if (
            brief.title
            or brief.background
            or brief.categories
            or brief.attachments
            or brief.study_type
        ):
            return False
        normalized = " ".join(message.lower().strip(" \t\r\n.!?,").split())
        return normalized in {
            "hi",
            "hello",
            "hey",
            "hi there",
            "hello there",
            "hey there",
            "good morning",
            "good afternoon",
            "good evening",
            "how are you",
            "how are you doing",
        }

    @staticmethod
    def _align_assistant_text(
        text: str, *, phase: BriefPhase, brief: StudyBrief
    ) -> str:
        """Prevent model prose from contradicting the server-computed phase."""
        if phase == "brief_ready":
            if "continue" not in text.lower():
                return (
                    text.rstrip()
                    + "\n\nReview the study brief below, then press **Continue with study**."
                )
            return text

        # The button is intentionally hidden while the brief is incomplete.
        lines = [
            line
            for line in text.splitlines()
            if not (
                "continue" in line.lower()
                and ("press" in line.lower() or "click" in line.lower())
            )
        ]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            return cleaned
        missing = set(brief.missing_fields)
        if "categories_min_2" in missing and brief.study_type == "grid":
            return "Got it. Please upload the images you want respondents to evaluate."
        if "categories_min_3" in missing and brief.study_type == "text":
            return (
                "I can draft the statements for this text study, or you can paste them "
                "here / upload a PDF or Word file and I’ll use that."
            )
        return "Great — what would you like to add or refine next?"

    def _call_ai(
        self,
        *,
        project: Project,
        chat: Chat,
        brief: StudyBrief,
        history: list[Any],
        user_message: str,
        new_attachments: list[AttachmentBrief],
    ) -> dict[str, Any]:
        if self._is_initial_greeting(user_message, brief):
            return {
                "assistant_message": (
                    "Hi! 👋 Let’s build your study. What would you like to learn or test?"
                ),
                "intent": "answer",
                "phase": "gathering",
                "suggested_chat_title": None,
                "missing_fields": brief.missing_fields,
                "study_brief": brief.model_dump(mode="json"),
            }
        if not openai_configured():
            return self._heuristic_ai(
                brief,
                user_message,
                new_attachments,
                siblings=self._sibling_study_summaries(project.id, chat.id),
            )

        transcript_lines: list[str] = []
        for msg in history[-16:]:
            role = getattr(msg, "role", "user")
            content = str(getattr(msg, "content", "") or "")
            if len(content) > 800:
                content = content[:800] + "…"
            transcript_lines.append(f"{role.upper()}: {content}")

        siblings = self._sibling_study_summaries(project.id, chat.id)
        user_prompt = STUDY_BRIEF_USER_TEMPLATE.format(
            project_name=project.name,
            project_id=str(project.id),
            project_sibling_studies_json=json.dumps(
                siblings, ensure_ascii=False, indent=2
            )
            if siblings
            else "[]  # no other chats in this project yet",
            current_brief_json=json.dumps(
                self._compact_brief_for_ai(brief), ensure_ascii=False, indent=2
            ),
            conversation_transcript="\n".join(transcript_lines) or "(empty)",
            user_message=user_message,
            new_attachments_json=json.dumps(
                [self._compact_attachment_for_ai(a) for a in new_attachments],
                ensure_ascii=False,
            ),
            document_excerpts=self._document_excerpts_for_ai(new_attachments),
        )
        return chat_json(system_prompt=STUDY_BRIEF_SYSTEM_PROMPT, user_prompt=user_prompt)

    def _heuristic_ai(
        self,
        brief: StudyBrief,
        user_message: str,
        new_attachments: list[AttachmentBrief],
        *,
        siblings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Deterministic fallback when OPENAI_API_KEY is missing (tests / local)."""
        text = user_message.strip()
        lower = text.lower()
        siblings = siblings or []
        brief = apply_inferred_audience(brief, text=text)
        brief = apply_text_study_hint(
            brief,
            text=text,
            has_images=any(
                (a.content_type or "").startswith("image/") for a in new_attachments
            ),
        )

        # Offline fallback: answer informational questions about sibling studies without
        # touching the current brief (no "Continue with study").
        reuse_words = ("continue", "reuse", "copy", "use ", "from ", "same as", "based on", "build on", "recreate")
        wants_reuse = any(w in lower for w in reuse_words)
        if siblings and not new_attachments and not wants_reuse:
            answer = self._answer_sibling_question(lower, siblings)
            if answer is not None:
                return {
                    "assistant_message": answer,
                    "intent": "answer",
                    "phase": self._phase_from_brief(brief),
                    "suggested_chat_title": None,
                    "missing_fields": brief.missing_fields,
                    "study_brief": brief.model_dump(mode="json"),
                }

        # Offline fallback: copy a sibling brief when the user asks to continue/reuse it.
        if siblings and wants_reuse:
            chosen: dict[str, Any] | None = None
            for sib in siblings:
                title = str(sib.get("title") or "").strip().lower()
                chat_title = str(sib.get("chat_title") or "").strip().lower()
                if title and title in lower:
                    chosen = sib
                    break
                if chat_title and chat_title in lower:
                    chosen = sib
                    break
            if chosen is None and len(siblings) == 1:
                chosen = siblings[0]
            full = chosen.get("full_brief") if chosen else None
            if isinstance(full, dict) and full:
                try:
                    copied = StudyBrief.model_validate(full)
                    copied.study_id = None
                    if copied.status == "created":
                        copied.status = "ready" if is_brief_ready_for_review(copied) else "gathering"
                    keep_attachments = list(brief.attachments)
                    brief = apply_defaults(copied)
                    if keep_attachments:
                        brief.attachments = keep_attachments
                    brief = apply_defaults(brief)
                    return {
                        "assistant_message": (
                            f"I’ve brought **{chosen.get('title') or chosen.get('chat_title') or 'that study'}** "
                            "into this chat as a draft. You can refine it here, then Continue / generate tasks / launch."
                        ),
                        "intent": "copy_sibling",
                        "phase": "brief_ready" if is_brief_ready_for_review(brief) else "gathering",
                        "suggested_chat_title": brief.title or None,
                        "missing_fields": brief.missing_fields,
                        "study_brief": brief.model_dump(mode="json"),
                    }
                except Exception:
                    logger.exception("Failed to copy sibling brief in heuristic AI")

        if new_attachments:
            brief.merge_attachments(new_attachments)
            for att in new_attachments:
                ctype = (att.content_type or "").lower()
                if ctype.startswith("image/") and brief.study_type is None:
                    brief.study_type = "grid"

        has_document = any((a.extracted_text or "").strip() for a in new_attachments)
        wants_text = (
            "text study" in lower
            or "text statements" in lower
            or "no image" in lower
            or "don't have image" in lower
            or "dont have image" in lower
            or "do not have image" in lower
            or "without image" in lower
            or "statements" in lower
        )
        if wants_text or (has_document and brief.study_type is None):
            brief.study_type = "text"
        elif "grid" in lower or "logo" in lower or (
            "image" in lower and "no image" not in lower and "without image" not in lower
        ):
            if brief.study_type != "text":
                brief.study_type = "grid"

        if brief.study_type == "text":
            if not brief.background.strip() and text:
                brief.background = text[:2000]
            brief = ensure_text_study_structure(brief)

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
            brief.main_question = (
                "How do you feel when seeing this logo for a pet-shedding brand?"
            )
            brief.orientation_text = (
                "You will see several logo options. Rate each one based on your "
                "first impression for a brand focused on pet shedding solutions."
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

        brief = ensure_default_classification(brief)
        brief = apply_defaults(brief)
        ready = is_brief_ready_for_review(brief)
        create_ready = is_brief_ready_for_create(brief)
        if ready and not create_ready and brief.study_type == "grid":
            msg = (
                "I’ve drafted your study brief. Review the summary card — for a grid study, "
                "upload an image for each element (or tell me you don’t have images and "
                "I’ll switch to a text study), then press Continue when you’re ready."
            )
        elif ready:
            msg = (
                "Your study brief looks complete. Review the summary and press "
                "Continue with study when you’re happy."
            )
        elif brief.study_type == "text":
            msg = (
                "I’ve started a **text study** — respondents will rate short statements "
                "instead of images. You can edit the statements on the brief card, paste "
                "more here, or upload a PDF / Word file."
            )
        elif brief.study_type is None:
            msg = (
                "Absolutely — **what are you trying to learn or test?**\n\n"
                "If you have images (logos, designs, packaging), upload them. "
                "If not, say so and I’ll build a text study with statements to rate."
            )
        else:
            msg = (
                "Absolutely — **what are you trying to learn or test?**\n\n"
                "Tell me a little about the product, idea, or decision behind the study, "
                "and I’ll shape the research approach for you."
            )
        return {
            "assistant_message": msg,
            "intent": "build",
            "phase": "brief_ready" if ready else "gathering",
            "suggested_chat_title": brief.title[:60] if brief.title else None,
            "missing_fields": brief.missing_fields,
            "study_brief": brief.model_dump(mode="json"),
        }

    @staticmethod
    def _match_sibling(lower: str, siblings: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Find the sibling a question refers to (by title/chat title), if any."""
        for sib in siblings:
            title = str(sib.get("title") or "").strip().lower()
            chat_title = str(sib.get("chat_title") or "").strip().lower()
            if title and title in lower:
                return sib
            if chat_title and chat_title in lower:
                return sib
        return None

    def _answer_sibling_question(
        self, lower: str, siblings: list[dict[str, Any]]
    ) -> str | None:
        """Deterministic answers for common sibling questions (offline fallback)."""
        list_words = ("list", "all studies", "other studies", "which studies", "what studies")
        url_words = ("share url", "share link", "preview url", "preview link", "link", "url")
        response_words = (
            "responses complete",
            "responses completed",
            "response complete",
            "how many response",
            "responses done",
            "collection",
            "completed",
        )
        status_words = ("status", "progress", "state")

        wants_list = any(w in lower for w in list_words)
        wants_url = any(w in lower for w in url_words)
        wants_responses = any(w in lower for w in response_words)
        wants_status = any(w in lower for w in status_words)

        target = self._match_sibling(lower, siblings)

        if wants_list and not target:
            lines = ["Here are the other studies in this project:"]
            for sib in siblings:
                lines.append(f"- **{sib.get('title') or sib.get('chat_title')}**")
                lines.append(f"  {self._sibling_status_line(sib)}")
            return "\n".join(lines)

        if wants_url:
            sib = target or (siblings[0] if len(siblings) == 1 else None)
            if sib is None:
                return (
                    "Which study do you mean? Tell me its name and I’ll share its "
                    "link. Studies here: "
                    + ", ".join(str(s.get("title") or s.get("chat_title")) for s in siblings)
                    + "."
                )
            return self._sibling_url_line(sib)

        if wants_responses or (wants_status and target):
            sib = target or (siblings[0] if len(siblings) == 1 else None)
            if sib is None:
                return (
                    "Which study do you want the response status for? Studies here: "
                    + ", ".join(str(s.get("title") or s.get("chat_title")) for s in siblings)
                    + "."
                )
            return self._sibling_status_line(sib, verbose=True)

        return None

    @staticmethod
    def _sibling_url_line(sib: dict[str, Any]) -> str:
        title = sib.get("title") or sib.get("chat_title") or "That study"
        share = sib.get("share_url")
        preview = sib.get("preview_url")
        if share:
            return f"Here’s the share link for **{title}**:\n\n{share}"
        if preview:
            return (
                f"**{title}** isn’t live yet, so there’s no public share link. "
                f"You can preview it here:\n\n{preview}\n\n"
                "Once it’s launched, a shareable participant link will be available."
            )
        return (
            f"**{title}** doesn’t have a link yet — it needs to be generated and "
            "launched first. Once it’s live I’ll have a shareable link for you."
        )

    @staticmethod
    def _sibling_status_line(sib: dict[str, Any], *, verbose: bool = False) -> str:
        title = sib.get("title") or sib.get("chat_title") or "That study"
        collection = sib.get("collection") or {}
        generation = sib.get("generation") or {}
        if collection:
            status = str(collection.get("status") or "").lower()
            completed = collection.get("completed") or collection.get(
                "respondents_completed"
            ) or 0
            total = collection.get("total_responses")
            requested = collection.get("respondents_requested")
            if status == "completed":
                head = f"**{title}**: collection is complete"
            elif status in {"running", "queued"}:
                head = f"**{title}**: collection is in progress"
            elif status == "failed":
                head = f"**{title}**: the last collection run failed"
            else:
                head = f"**{title}**: {status or 'not started'}"
            detail = f" — {completed} completed"
            if requested:
                detail += f" of {requested} requested"
            if total is not None:
                detail += f" ({total} started so far)"
            return head + detail + "."
        if generation and generation.get("launched"):
            return f"**{title}**: live, but no respondent collection has run yet."
        status = str(sib.get("brief_status") or "draft")
        pretty = {
            "created": "created (not yet launched)",
            "ready": "ready to launch",
            "gathering": "still being drafted",
        }.get(status, status)
        return f"**{title}**: {pretty}."

    def _brief_from_ai(self, current: StudyBrief, payload: dict[str, Any]) -> StudyBrief:
        raw = payload.get("study_brief")
        if not isinstance(raw, dict):
            return current
        try:
            incoming = StudyBrief.model_validate(raw)
        except Exception:
            logger.warning("AI study_brief failed validation; keeping prior brief")
            return current
        # Preserve study_id / created status for THIS chat only — never adopt a sibling's live id.
        if current.study_id:
            incoming.study_id = current.study_id
        else:
            incoming.study_id = None
            if incoming.status == "created":
                incoming.status = (
                    "ready" if is_brief_ready_for_review(incoming) else "gathering"
                )
        if current.status == "created":
            incoming.status = "created"
        if not incoming.attachments and current.attachments:
            incoming.attachments = current.attachments
        # Never lose audience the user already provided if the AI drops it.
        if (
            not incoming.audience.number_of_respondents
            and current.audience.number_of_respondents
        ):
            incoming.audience.number_of_respondents = (
                current.audience.number_of_respondents
            )
        if (
            not incoming.audience.age_distribution
            and current.audience.age_distribution
        ):
            incoming.audience.age_distribution = current.audience.age_distribution
            incoming.audience.age_segments = current.audience.age_segments
        if not incoming.audience.countries and current.audience.countries:
            incoming.audience.countries = current.audience.countries
        raw_audience = raw.get("audience")
        if isinstance(raw_audience, dict):
            if "gender_male" not in raw_audience:
                incoming.audience.gender_male = current.audience.gender_male
            if "gender_female" not in raw_audience:
                incoming.audience.gender_female = current.audience.gender_female
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
