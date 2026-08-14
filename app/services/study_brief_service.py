"""Conversational study-brief AI orchestration."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai_prompts.study_brief import (
    STUDY_BRIEF_SYSTEM_PROMPT,
    render_study_brief_user_prompt,
)
from app.core.config import get_settings
from app.core.exceptions import AppError, NotFoundError
from app.db.models.chat import Chat
from app.db.models.project import Project
from app.db.models.user import User
from app.db.models.study_brief_version import StudyBriefVersion
from app.repositories.project_repository import ProjectRepository
from app.repositories.study_brief_version_repository import (
    StudyBriefVersionRepository,
)
from app.repositories.study_generation_repository import StudyGenerationRepository
from app.repositories.synthetic_collection_repository import (
    SyntheticCollectionRepository,
)
from app.schemas.project import MessageOut
from app.schemas.study_brief import (
    AttachmentBrief,
    BriefPhase,
    BriefVersionListOut,
    BriefVersionMeta,
    BriefVersionOut,
    ElementBrief,
    StudyBrief,
    StudyBriefUpdate,
    StudyConfirmResponse,
)
from app.services.study_payload import diff_brief_fields, fingerprint_brief
from app.services.audience_infer import (
    apply_inferred_audience,
    apply_text_study_hint,
)
from app.services.folder_brief import (
    apply_folder_categories,
    ensure_default_classification,
)
from app.services.openai_client import chat_json, chat_stream, openai_configured
from app.services.study_brief_validator import (
    apply_defaults,
    compute_missing_fields,
    is_brief_ready_for_create,
    is_brief_ready_for_review,
)
from app.services.study_create_service import (
    create_draft_study_from_brief,
    sync_study_metadata_from_brief,
)
from app.services.synthetic_capacity import min_classification_question_count
from app.services.text_brief import (
    dedupe_similar_categories,
    ensure_text_study_structure,
)

logger = logging.getLogger(__name__)


class StudyBriefService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = ProjectRepository(db)
        self.generation_repo = StudyGenerationRepository(db)
        self.collection_repo = SyntheticCollectionRepository(db)
        self.version_repo = StudyBriefVersionRepository(db)

    def get_brief(self, user: User, chat_id: UUID) -> tuple[BriefPhase, StudyBrief]:
        chat, _project = self._owned_chat(user, chat_id)
        brief = self._load_brief(chat)
        try:
            if self._ensure_initial_version(chat, brief):
                self.db.commit()
        except Exception:
            logger.exception("Could not seed brief version history")
            self.db.rollback()
            chat, _project = self._owned_chat(user, chat_id)
            brief = self._load_brief(chat)
        phase = self._phase_from_brief(brief)
        return phase, brief

    def version_meta(self, chat_id: UUID) -> BriefVersionMeta | None:
        try:
            latest = self.version_repo.latest(chat_id)
        except Exception:
            logger.exception("Could not load brief version meta")
            self.db.rollback()
            return None
        if latest is None:
            return None
        return BriefVersionMeta(
            version=latest.version,
            total=latest.version,
            summary=latest.summary,
            source=latest.source,
            changed_fields=list(latest.changed_fields or []),
            created_at=latest.created_at,
        )

    def list_versions(self, user: User, chat_id: UUID) -> BriefVersionListOut:
        chat, _project = self._owned_chat(user, chat_id)
        brief = self._load_brief(chat)
        try:
            if self._ensure_initial_version(chat, brief):
                self.db.commit()
            rows = self.version_repo.list_for_chat(chat.id)
        except Exception:
            logger.exception("Could not list brief versions")
            self.db.rollback()
            return BriefVersionListOut(current_version=0, total=0, versions=[])
        versions: list[BriefVersionOut] = []
        for row in rows:
            try:
                snap = apply_defaults(StudyBrief.model_validate(row.brief_json or {}))
            except Exception:
                continue
            versions.append(
                BriefVersionOut(
                    version=row.version,
                    summary=row.summary,
                    source=row.source,
                    changed_fields=list(row.changed_fields or []),
                    created_at=row.created_at,
                    study_brief=snap,
                )
            )
        current = versions[-1].version if versions else 0
        return BriefVersionListOut(
            current_version=current,
            total=len(versions),
            versions=versions,
        )

    def restore_version(
        self, user: User, chat_id: UUID, version: int
    ) -> tuple[BriefPhase, StudyBrief, list[str]]:
        chat, _project = self._owned_chat(user, chat_id)
        if self._is_study_live(chat):
            raise AppError(
                "This study is live. Draft versions can’t be restored now.",
                status_code=409,
            )
        current = self._load_brief(chat)
        restored = self._brief_from_version(chat, version)
        if restored is None:
            raise AppError("That draft version wasn’t found.", status_code=404)
        restored.study_id = current.study_id
        if current.status == "created":
            restored.status = "created"
        restored.merge_attachments(current.attachments)
        restored = apply_defaults(restored)
        if not self._verify_ready_brief(current, restored):
            raise AppError(
                "Restoring that version would break the current draft. Try another version.",
                status_code=422,
            )
        changed = diff_brief_fields(current, restored)
        if self._changes_require_regeneration(chat.id, changed):
            raise AppError(
                "Restoring this version changes generated task content. "
                "Confirm task regeneration before applying it.",
                status_code=409,
            )
        sync_study_metadata_from_brief(
            self.db, brief=restored, changed_fields=changed
        )
        self._snapshot_if_changed(
            chat,
            current,
            restored,
            summary=f"Restored from version {version}",
            source="restore",
            changed=changed,
        )
        self._save_brief(chat, restored)
        self.db.commit()
        self.db.refresh(chat)
        return self._phase_from_brief(restored), restored, changed

    def update_brief(
        self, user: User, chat_id: UUID, patch: StudyBriefUpdate
    ) -> tuple[BriefPhase, StudyBrief]:
        chat, _project = self._owned_chat(user, chat_id)
        brief = self._load_brief(chat)
        if self._is_study_live(chat):
            raise AppError(
                "This study is live. Task-affecting edits are locked.",
                status_code=409,
            )

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
        if not self._verify_ready_brief(brief, merged):
            raise AppError(
                "Those changes would leave the draft incomplete. Please review and try again.",
                status_code=422,
            )
        changed = diff_brief_fields(brief, merged)
        if self._changes_require_regeneration(chat.id, changed):
            raise AppError(
                "These changes affect generated tasks. "
                "Confirm task regeneration before applying them.",
                status_code=409,
            )
        sync_study_metadata_from_brief(
            self.db, brief=merged, changed_fields=changed
        )
        self._snapshot_if_changed(
            chat,
            brief,
            merged,
            summary=self._change_summary(changed) or "Manual edit",
            source="user",
            changed=changed,
        )
        self._save_brief(chat, merged)
        if changed:
            phase = self._phase_from_brief(merged)
            note = self._with_change_details(
                "I saved your edits to the study brief.",
                merged,
                changed,
                before=brief,
            )
            self.repo.create_message(
                chat_id=chat.id,
                role="assistant",
                content=note,
                metadata={
                    "kind": "study_brief",
                    "phase": phase,
                    "changed_fields": changed,
                    "intent": "build",
                    "source": "manual_edit",
                },
            )
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
        baseline = brief.model_copy(deep=True)

        attachments = attachments or []
        body = content.strip()
        if not body and not attachments:
            raise AppError("Please enter a message or attach a file.", status_code=422)

        # After launch, allow chat but freeze the study brief (no structural edits).
        if self._is_study_live(chat):
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
            intent = self._resolve_intent(
                ai_payload,
                has_attachments=bool(attachments),
                user_message=body,
            )
            was_ready = is_brief_ready_for_review(brief) or brief.status in {
                "ready",
                "created",
            }

            new_brief, phase, changed, intent = self._apply_ai_brief_update(
                chat=chat,
                brief=brief,
                ai_payload=ai_payload,
                intent=intent,
                corpus=corpus,
                attachments=attachments,
                baseline=baseline,
            )
            pending_regen: dict[str, Any] | None = None
            if intent != "answer" and self._changes_require_regeneration(
                chat.id, changed
            ):
                pending_regen = self._regeneration_proposal(brief, new_brief, changed)
                new_brief = brief
                phase = self._phase_from_brief(brief)
                changed = []
                intent = "answer"
            if not assistant_text:
                assistant_text = (
                    "Here’s what I found. What would you like to do next?"
                    if intent == "answer"
                    else "Thanks — I’ve updated your study brief."
                )
            if pending_regen:
                assistant_text = pending_regen["message"]
            elif intent == "answer" and self._looks_like_edit(body) and not changed:
                assistant_text = (
                    "I couldn’t apply that change to the draft. "
                    "Try again with a more specific edit — for example which "
                    "category to remove, or how you want the questions rewritten."
                )
            elif intent != "answer":
                assistant_text = self._align_assistant_text(
                    assistant_text,
                    phase=phase,
                    brief=new_brief,
                    skip_continue=was_ready,
                )
                assistant_text = self._with_change_details(
                    assistant_text, new_brief, changed, before=baseline
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
                "changed_fields": changed,
                "intent": intent,
            }
            if pending_regen:
                assistant_meta["kind"] = "regeneration_request"
                assistant_meta["pending_patch"] = pending_regen["patch"]
                assistant_meta["changed_fields"] = pending_regen["changed_fields"]
                assistant_meta["pending_preview"] = pending_regen["preview"]
            assistant_msg = self.repo.create_message(
                chat_id=chat.id,
                role="assistant",
                content=assistant_text,
                metadata=assistant_meta,
            )
            if intent != "answer":
                sync_study_metadata_from_brief(
                    self.db, brief=new_brief, changed_fields=changed
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
        if self._is_study_live(chat):
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
            baseline = brief.model_copy(deep=True)
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
            intent = self._resolve_intent(
                ai_payload,
                has_attachments=False,
                user_message=history[-1].content,
            )
            was_ready = is_brief_ready_for_review(brief) or brief.status in {
                "ready",
                "created",
            }
            new_brief, phase, changed, intent = self._apply_ai_brief_update(
                chat=chat,
                brief=brief,
                ai_payload=ai_payload,
                intent=intent,
                corpus=corpus,
                attachments=[],
                baseline=baseline,
            )
            pending_regen: dict[str, Any] | None = None
            if intent != "answer" and self._changes_require_regeneration(
                chat.id, changed
            ):
                pending_regen = self._regeneration_proposal(brief, new_brief, changed)
                new_brief = brief
                phase = self._phase_from_brief(brief)
                changed = []
                intent = "answer"
            if not assistant_text:
                assistant_text = (
                    "Here’s what I found. What would you like to do next?"
                    if intent == "answer"
                    else "Thanks — I’ve updated your study brief."
                )
            last_user = str(getattr(history[-1], "content", "") or "")
            if pending_regen:
                assistant_text = pending_regen["message"]
            elif intent == "answer" and self._looks_like_edit(last_user) and not changed:
                assistant_text = (
                    "I couldn’t apply that change to the draft. "
                    "Try again with a more specific edit — for example which "
                    "category to remove, or how you want the questions rewritten."
                )
            elif intent != "answer":
                assistant_text = self._align_assistant_text(
                    assistant_text,
                    phase=phase,
                    brief=new_brief,
                    skip_continue=was_ready,
                )
                assistant_text = self._with_change_details(
                    assistant_text, new_brief, changed, before=baseline
                )

            suggested = ai_payload.get("suggested_chat_title")
            suggested_title = (
                str(suggested).strip()[:60]
                if isinstance(suggested, str) and suggested.strip()
                else None
            )
            continue_meta: dict[str, Any] = {
                "kind": "study_brief",
                "phase": phase,
                "attachment_count": len(new_brief.attachments),
                "changed_fields": changed,
                "intent": intent,
            }
            if pending_regen:
                continue_meta["kind"] = "regeneration_request"
                continue_meta["pending_patch"] = pending_regen["patch"]
                continue_meta["changed_fields"] = pending_regen["changed_fields"]
                continue_meta["pending_preview"] = pending_regen["preview"]
            assistant_msg = self.repo.create_message(
                chat_id=chat.id,
                role="assistant",
                content=assistant_text,
                metadata=continue_meta,
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
        # Keep a clipped excerpt so later refine turns can still read the source docs.
        for att in brief.attachments:
            text = (att.extracted_text or "").strip()
            att.extracted_text = text[:12_000] or None
        chat.study_brief = brief.model_dump(mode="json")
        self.repo.save_chat(chat)

    @staticmethod
    def _phase_from_brief(brief: StudyBrief) -> BriefPhase:
        if brief.status == "created" or brief.study_id:
            return "created"
        if brief.status == "ready" or is_brief_ready_for_review(brief):
            return "brief_ready"
        return "gathering"

    def _is_study_live(self, chat: Chat) -> bool:
        try:
            run = self.generation_repo.latest_for_chat(chat.id)
        except Exception:
            return False
        return bool(run and str(run.study_status) == "live")

    def _changes_require_regeneration(
        self, chat_id: UUID, changed: list[str]
    ) -> bool:
        """Protect a generated matrix from silent task-affecting brief edits."""
        if not {"study_type", "categories"}.intersection(changed):
            return False
        try:
            run = self.generation_repo.latest_for_chat(chat_id)
        except Exception:
            return False
        if run is None:
            return False
        return str(run.status) in {
            "queued",
            "generating",
            "saving",
            "ready",
            "failed",
            "cancelled",
            "launched",
        }

    @staticmethod
    def _stimulus_text(element: ElementBrief) -> str:
        return (element.content or element.name or "").strip()

    @classmethod
    def _stimulus_diff(
        cls, current: StudyBrief, proposed: StudyBrief
    ) -> list[dict[str, str]]:
        """Per-statement before/after so the customer sees exactly what was prepared."""
        before = {c.name.strip(): c for c in current.categories}
        after = {c.name.strip(): c for c in proposed.categories}
        items: list[dict[str, str]] = []

        for name, new_cat in after.items():
            old_cat = before.get(name)
            if old_cat is None:
                for element in new_cat.elements:
                    items.append(
                        {
                            "type": "added",
                            "category": name,
                            "before": "",
                            "after": cls._stimulus_text(element),
                        }
                    )
                continue

            old_texts = [cls._stimulus_text(e) for e in old_cat.elements]
            new_texts = [cls._stimulus_text(e) for e in new_cat.elements]
            old_set, new_set = set(old_texts), set(new_texts)
            dropped = [t for t in old_texts if t not in new_set]
            introduced = [t for t in new_texts if t not in old_set]

            # A same-position swap reads as an edit; anything else is an add/remove.
            for old_text, new_text in zip(dropped, introduced):
                items.append(
                    {
                        "type": "edited",
                        "category": name,
                        "before": old_text,
                        "after": new_text,
                    }
                )
            for extra in introduced[len(dropped) :]:
                items.append(
                    {"type": "added", "category": name, "before": "", "after": extra}
                )
            for removed in dropped[len(introduced) :]:
                items.append(
                    {"type": "removed", "category": name, "before": removed, "after": ""}
                )

        for name, old_cat in before.items():
            if name in after:
                continue
            for element in old_cat.elements:
                items.append(
                    {
                        "type": "removed",
                        "category": name,
                        "before": cls._stimulus_text(element),
                        "after": "",
                    }
                )

        return items

    @classmethod
    def _regeneration_proposal(
        cls,
        current: StudyBrief,
        proposed: StudyBrief,
        changed: list[str],
    ) -> dict[str, Any]:
        """Hold a stimulus edit as a proposal the customer confirms before regenerating."""
        patch: dict[str, Any] = {
            "categories": [c.model_dump(mode="json") for c in proposed.categories]
        }
        if "study_type" in changed and proposed.study_type:
            patch["study_type"] = proposed.study_type

        noun = "statements" if proposed.study_type == "text" else "images"
        items = cls._stimulus_diff(current, proposed)
        edited = sum(1 for i in items if i["type"] == "edited")
        added = sum(1 for i in items if i["type"] == "added")
        removed = sum(1 for i in items if i["type"] == "removed")

        parts: list[str] = []
        if edited:
            parts.append(f"{edited} rewritten")
        if added:
            parts.append(f"{added} added")
        if removed:
            parts.append(f"{removed} removed")
        summary = ", ".join(parts) if parts else f"updated {noun}"

        return {
            "patch": patch,
            "changed_fields": changed,
            "preview": {
                "summary": summary,
                "items": items[:12],
                "total": len(items),
            },
            "message": (
                f"Here’s the {noun} update I prepared ({summary}). Your tasks were "
                f"already generated from the current {noun}, so applying this replaces "
                "the existing task matrix — confirm below and I’ll regenerate."
            ),
        }

    def _all_document_excerpts(
        self, brief: StudyBrief, new_attachments: list[AttachmentBrief]
    ) -> str:
        seen: set[str] = set()
        combined: list[AttachmentBrief] = []
        for att in list(new_attachments) + list(brief.attachments):
            key = att.url or att.filename or ""
            if not key or key in seen:
                continue
            if not (att.extracted_text or "").strip():
                continue
            seen.add(key)
            combined.append(att)
        return self._document_excerpts_for_ai(combined)

    def _version_history_for_ai(self, chat_id: UUID) -> str:
        try:
            rows = self.version_repo.list_for_chat(chat_id)
        except Exception:
            logger.exception("Could not load version history for AI")
            self.db.rollback()
            return "[]  # version history unavailable"
        if not rows:
            return "[]  # no versions yet — this is the first draft"
        out: list[dict[str, Any]] = []
        for row in rows[-8:]:
            try:
                snap = StudyBrief.model_validate(row.brief_json or {})
            except Exception:
                continue
            out.append(
                {
                    "version": row.version,
                    "summary": row.summary,
                    "changed_fields": row.changed_fields or [],
                    "title": snap.title,
                    "respondents": snap.audience.number_of_respondents,
                    "categories": [
                        {
                            "name": cat.name,
                            "statements": [
                                (el.content or el.name)[:150] for el in cat.elements[:8]
                            ],
                        }
                        for cat in snap.categories[:8]
                    ],
                    "audience": snap.audience.model_dump(mode="json"),
                }
            )
        return json.dumps(out, ensure_ascii=False, indent=2)

    def _brief_json_for_version(self, brief: StudyBrief) -> dict[str, Any]:
        data = brief.model_dump(mode="json")
        for att in data.get("attachments") or []:
            if isinstance(att, dict):
                att["extracted_text"] = None
        return data

    def _ensure_initial_version(self, chat: Chat, brief: StudyBrief) -> bool:
        if self.version_repo.max_version(chat.id) > 0:
            return False
        if not (brief.title.strip() or brief.categories):
            return False
        self._snapshot_brief(
            chat,
            brief,
            summary="Initial draft",
            source="system",
            changed=[],
        )
        self.db.flush()
        return True

    def _snapshot_brief(
        self,
        chat: Chat,
        brief: StudyBrief,
        *,
        summary: str,
        source: str,
        changed: list[str],
    ) -> StudyBriefVersion:
        next_n = self.version_repo.max_version(chat.id) + 1
        row = StudyBriefVersion(
            chat_id=chat.id,
            version_number=next_n,
            summary=(summary or "Draft update")[:240],
            source=source,
            snapshot_json=self._brief_json_for_version(brief),
            fingerprint=fingerprint_brief(brief),
            changed_paths=changed,
        )
        return self.version_repo.add(row)

    def _snapshot_if_changed(
        self,
        chat: Chat,
        before: StudyBrief,
        after: StudyBrief,
        *,
        summary: str,
        source: str,
        changed: list[str],
    ) -> None:
        if self.version_repo.max_version(chat.id) == 0 and (
            before.title.strip() or before.categories
        ):
            self._snapshot_brief(
                chat,
                before,
                summary="Initial draft",
                source="system",
                changed=[],
            )
        if not changed and self.version_repo.max_version(chat.id) > 0:
            return
        try:
            self._snapshot_brief(
                chat,
                after,
                summary=summary or self._change_summary(changed) or "Draft update",
                source=source,
                changed=changed,
            )
        except Exception:
            logger.exception("Could not snapshot brief version")
            try:
                self.db.rollback()
            except Exception:
                pass

    @staticmethod
    def _change_summary(changed: list[str], restore_n: int | None = None) -> str:
        if restore_n:
            return f"Restored from version {restore_n}"
        labels = {
            "title": "Updated title",
            "background": "Updated background",
            "main_question": "Updated main question",
            "orientation_text": "Updated orientation",
            "rating_scale": "Updated rating scale",
            "categories": "Updated categories / statements",
            "classification_questions": "Updated screening questions",
            "audience": "Updated audience",
        }
        if not changed:
            return ""
        if len(changed) == 1:
            return labels.get(changed[0], f"Updated {changed[0]}")
        pretty = [labels.get(item, item) for item in changed[:4]]
        return "; ".join(pretty)

    def _verify_ready_brief(self, before: StudyBrief, after: StudyBrief) -> bool:
        """Reject edits that collapse a complete draft into an invalid one."""
        was_ready = (
            before.status in {"ready", "created"}
            or is_brief_ready_for_review(before)
        )
        if not was_ready:
            return True
        return is_brief_ready_for_review(after)

    def _brief_from_version(self, chat: Chat, version: int) -> StudyBrief | None:
        row = self.version_repo.get(chat.id, version)
        if row is None:
            return None
        try:
            return apply_defaults(StudyBrief.model_validate(row.brief_json or {}))
        except Exception:
            return None

    def _restore_from_payload(
        self, chat: Chat, current: StudyBrief, payload: dict[str, Any]
    ) -> tuple[StudyBrief, list[str], str] | None:
        raw_n = payload.get("restore_version")
        try:
            version = int(raw_n)
        except (TypeError, ValueError):
            latest = self.version_repo.max_version(chat.id)
            version = max(1, latest - 1) if latest > 1 else 0
        if version < 1:
            return None
        old = self._brief_from_version(chat, version)
        if old is None:
            return None
        fields = payload.get("restore_fields") or ["all"]
        if isinstance(fields, str):
            fields = [fields]
        fields = [str(item).strip().lower() for item in fields if str(item).strip()]
        data = current.model_dump(mode="json")
        old_data = old.model_dump(mode="json")
        copy_all = not fields or "all" in fields or "*" in fields
        keys = (
            list(old_data.keys())
            if copy_all
            else [key for key in fields if key in old_data]
        )
        protected = {"study_id", "status", "attachments", "missing_fields"}
        for key in keys:
            if key in protected:
                continue
            data[key] = old_data[key]
        try:
            merged = StudyBrief.model_validate(data)
        except Exception:
            return None
        merged.study_id = current.study_id
        if current.status == "created":
            merged.status = "created"
        merged.merge_attachments(current.attachments)
        changed = diff_brief_fields(current, merged)
        return merged, changed, f"Restored from version {version}"

    def _apply_ai_brief_update(
        self,
        *,
        chat: Chat,
        brief: StudyBrief,
        ai_payload: dict[str, Any],
        intent: str,
        corpus: str,
        attachments: list[AttachmentBrief],
        baseline: StudyBrief | None = None,
    ) -> tuple[StudyBrief, BriefPhase, list[str], str]:
        # `brief` may already carry pre-AI inference (audience, study type, folder
        # categories). Diff against the stored draft so those edits are reported.
        prior = baseline or brief
        if intent == "answer" and self._looks_like_edit(corpus):
            intent = "build"
        if intent == "answer":
            return brief, self._phase_from_brief(brief), [], "answer"

        changed: list[str] = []
        summary = ""
        if intent == "restore" or ai_payload.get("restore_version") not in (
            None,
            "",
            0,
            "0",
        ):
            restored = self._restore_from_payload(chat, brief, ai_payload)
            if restored is not None:
                new_brief, changed, summary = restored
                intent = "restore"
            else:
                new_brief = self._brief_from_ai(brief, ai_payload)
                intent = "build"
        else:
            new_brief = self._brief_from_ai(brief, ai_payload)

        new_brief.merge_attachments(brief.attachments)
        new_brief = apply_folder_categories(
            new_brief, attachments or brief.attachments
        )
        new_brief = apply_inferred_audience(new_brief, text=corpus)
        new_brief = apply_text_study_hint(
            new_brief,
            text=corpus,
            has_images=any(
                (a.content_type or "").startswith("image/") for a in (attachments or [])
            ),
        )
        last_ask = (corpus or "").strip().split("\n")[-1].lower()
        requested_cats = self._requested_category_count(last_ask)
        new_brief = dedupe_similar_categories(new_brief)
        new_brief = ensure_text_study_structure(
            new_brief,
            target_categories=requested_cats,
        )
        # A question the model dropped this turn was deleted on purpose, so the
        # capacity backfill must not put that exact question back.
        kept = {
            q.question_text.strip().lower()
            for q in new_brief.classification_questions
        }
        removed_questions = [
            q.question_text
            for q in prior.classification_questions
            if q.question_text.strip() and q.question_text.strip().lower() not in kept
        ]
        new_brief = ensure_default_classification(
            new_brief, avoid_texts=removed_questions
        )
        new_brief = apply_defaults(new_brief)
        if brief.status == "created":
            new_brief.status = "created"
            new_brief.study_id = brief.study_id
        elif is_brief_ready_for_review(new_brief):
            new_brief.status = "ready"
        else:
            new_brief.status = "gathering"

        if not self._verify_ready_brief(brief, new_brief):
            logger.warning(
                "AI brief update rejected: would leave a ready draft incomplete"
            )
            return brief, self._phase_from_brief(brief), [], "answer"

        if not changed:
            changed = diff_brief_fields(prior, new_brief)
        logger.info("AI brief update changed=%s", changed)
        if not changed:
            return brief, self._phase_from_brief(brief), [], "answer"

        self._snapshot_if_changed(
            chat,
            prior,
            new_brief,
            summary=summary or self._change_summary(changed),
            source="restore" if intent == "restore" else "ai",
            changed=changed,
        )
        return new_brief, self._phase_from_brief(new_brief), changed, intent

    def _heuristic_restore(
        self, chat: Chat, brief: StudyBrief, text: str
    ) -> dict[str, Any] | None:
        lower = text.lower()
        looks_restore = any(
            word in lower
            for word in (
                "previous",
                "undo",
                "get that back",
                "get the previous",
                "restore",
                "version ",
                "go back",
            )
        )
        if not looks_restore:
            return None
        latest = self.version_repo.max_version(chat.id)
        if latest < 2:
            return None
        version = latest - 1
        for token in lower.replace("version", " version ").split():
            if token.isdigit():
                n = int(token)
                if 1 <= n <= latest:
                    version = n
                    break
        fields = ["categories"] if "statement" in lower or "categor" in lower else ["all"]
        if "respondent" in lower or "sample" in lower or "audience" in lower:
            fields = ["audience"]
        return {
            "assistant_message": (
                f"Restored {', '.join(fields)} from version {version}."
            ),
            "intent": "restore",
            "restore_version": version,
            "restore_fields": fields,
            "phase": self._phase_from_brief(brief),
            "suggested_chat_title": None,
            "missing_fields": brief.missing_fields,
            "study_brief": brief.model_dump(mode="json"),
        }

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
    def _edit_targets(text: str, payload: dict[str, Any] | None = None) -> list[str]:
        """Fields the user / model asked to change on this turn."""
        last = (text or "").strip().split("\n")[-1].lower()
        targets: list[str] = []
        raw_fields = (payload or {}).get("changed_fields") or []
        if isinstance(raw_fields, str):
            raw_fields = [raw_fields]
        for item in raw_fields:
            key = str(item).strip()
            if key in StudyBriefService._BRIEF_MERGE_KEYS and key not in targets:
                targets.append(key)
        if any(word in last for word in ("question", "screener", "classification")):
            if "classification_questions" not in targets:
                targets.append("classification_questions")
        if any(word in last for word in ("categor", "statement", "duplicate")):
            if "categories" not in targets:
                targets.append("categories")
        if any(word in last for word in ("audience", "respondent", "sample", "country")):
            if "audience" not in targets:
                targets.append("audience")
        return targets

    def _overlay_gpt_fields(
        self,
        brief: StudyBrief,
        payload: dict[str, Any],
        targets: list[str],
    ) -> StudyBrief:
        """Put GPT's requested fields back on top after our normalizers run."""
        raw = payload.get("study_brief")
        if not isinstance(raw, dict) or not targets:
            return brief
        data = brief.model_dump(mode="json")
        for key in targets:
            if key not in raw:
                continue
            trial = dict(data)
            trial[key] = raw[key]
            try:
                StudyBrief.model_validate(trial)
            except Exception:
                logger.warning("GPT field %s failed validation; left previous value", key)
                continue
            data[key] = raw[key]
        return StudyBrief.model_validate(data)

    _WORD_COUNTS = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }

    @staticmethod
    def _requested_category_count(text: str) -> int | None:
        last = (text or "").strip().split("\n")[-1].lower()
        if "categor" not in last and "cats" not in last:
            return None
        targeted = re.search(
            r"(?:has to be|have to be|want(?: to have)?|need|make it|to)\s+(\d+)",
            last,
        )
        if targeted:
            n = int(targeted.group(1))
            if 3 <= n <= 20:
                return n
        for word, n in StudyBriefService._WORD_COUNTS.items():
            if re.search(
                rf"(?:has to be|have to be|want(?: to have)?|need|make it)\s+{word}\b",
                last,
            ) and 3 <= n <= 20:
                return n
        digits = [int(token) for token in re.findall(r"\d+", last)]
        valid = [n for n in digits if 3 <= n <= 20]
        if valid:
            return valid[-1]
        return None

    @staticmethod
    def _looks_like_edit(text: str) -> bool:
        """True when the latest user line is asking to change the draft."""
        last = (text or "").strip().split("\n")[-1].lower()
        if not last:
            return False
        edit_words = (
            "change",
            "update",
            "edit",
            "fix",
            "remove",
            "delete",
            "add ",
            "make ",
            "want",
            "duplicate",
            "duplicated",
            "rename",
            "replace",
            "rewrite",
            "set ",
            "swap",
            "drop ",
            "merge",
        )
        asked_to_change = any(word in last for word in edit_words)
        if not asked_to_change:
            return False
        if last.endswith("?") and not any(
            word in last
            for word in ("change", "update", "fix", "remove", "add", "duplicate")
        ):
            return False
        return True

    @staticmethod
    def _resolve_intent(
        payload: dict[str, Any],
        *,
        has_attachments: bool,
        user_message: str = "",
    ) -> str:
        """Normalize the AI's turn intent.

        - "answer": informational reply, brief must not change.
        - "copy_sibling": copy a sibling brief into this chat.
        - "build": create/refine this chat's own brief (default).
        Uploading files always implies building, regardless of what the model says.
        A clear edit request is never treated as answer.
        """
        if has_attachments:
            return "build"
        raw = str(payload.get("intent") or "").strip().lower()
        if raw == "answer" and StudyBriefService._looks_like_edit(user_message):
            return "build"
        if raw in {"answer", "copy_sibling", "build", "restore"}:
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
    def _with_change_details(
        text: str,
        brief: StudyBrief,
        changed: list[str],
        before: StudyBrief | None = None,
    ) -> str:
        """Append a concrete changelog so the chat shows what the draft now contains."""
        if not changed:
            return text
        if "**What I changed**" in text:
            return text
        lines = ["", "**What I changed**"]
        if "categories" in changed:
            names = [cat.name for cat in brief.categories if cat.name.strip()]
            n_stmt = sum(len(cat.elements) for cat in brief.categories)
            lines.append(
                f"- Categories ({len(names)}): " + ", ".join(names[:8])
            )
            lines.append(f"- Statements: {n_stmt}")
        if "classification_questions" in changed:
            old_texts = (
                {
                    (q.question_text or "").strip()
                    for q in before.classification_questions
                }
                if before
                else set()
            )
            new_texts = {
                (q.question_text or "").strip()
                for q in brief.classification_questions
                if (q.question_text or "").strip()
            }
            new_questions = [text for text in new_texts if text not in old_texts]
            removed = sorted(old_texts - new_texts - {""})
            lines.append(
                f"- Screening questions: {len(brief.classification_questions)}"
                + (f" ({len(new_questions)} new)" if new_questions else "")
            )
            for question in sorted(new_questions)[:5]:
                lines.append(f"  - Added: {question}")
            for question in removed[:5]:
                lines.append(f"  - Removed: {question}")
            floor = min_classification_question_count(
                brief.audience.number_of_respondents
            )
            if removed and new_questions and len(new_texts) <= floor:
                lines.append(
                    f"  - {brief.audience.number_of_respondents or 'This'} respondents "
                    f"need at least {floor} screening questions, so I swapped in a "
                    "replacement instead of dropping the count."
                )
        if "audience" in changed:
            aud = brief.audience
            bits: list[str] = []
            if aud.number_of_respondents:
                bits.append(f"{aud.number_of_respondents} respondents")
            if aud.countries:
                bits.append(", ".join(aud.countries[:4]))
            ages = [seg for seg in (aud.age_segments or aud.age_distribution.keys())]
            if ages:
                bits.append(f"ages {', '.join(ages[:6])}")
            lines.append("- Audience: " + (" · ".join(bits) or "—"))
        extra = {
            "title": f"- Title: {brief.title}" if brief.title else "- Title",
            "background": "- Background",
            "main_question": (
                f"- Main question: {brief.main_question}"
                if brief.main_question
                else "- Main question"
            ),
            "orientation_text": "- Orientation",
            "rating_scale": "- Rating scale",
        }
        for key in changed:
            if key in extra:
                lines.append(extra[key])
        if len(lines) <= 1:
            return text
        return text.rstrip() + "\n" + "\n".join(lines)

    @staticmethod
    def _align_assistant_text(
        text: str,
        *,
        phase: BriefPhase,
        brief: StudyBrief,
        skip_continue: bool = False,
    ) -> str:
        """Prevent model prose from contradicting the server-computed phase."""
        if skip_continue:
            return text
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

    THINKING_SYSTEM = """
You are MindSurve Study Architect thinking out loud while you work.
Speak in short first-person sentences about THIS request only.
Do not produce JSON. Do not mention JSON, fields, schema, or file format.
Do not greet. Do not ask the user questions.
Narrate the study itself — categories, statements, screening questions,
audience, or the edit they asked for.
Keep going until you have walked through the work. Write 16–22 short sentences
so the user can follow along while the draft is being built.
""".strip()

    def iter_thinking_tokens(
        self,
        user: User,
        chat_id: UUID,
        *,
        content: str,
        attachments: list[AttachmentBrief] | None = None,
    ):
        """Stream live model thoughts for the current user request."""
        chat, project = self._owned_chat(user, chat_id)
        brief = self._load_brief(chat)
        attachments = attachments or []
        if not openai_configured():
            return
        excerpt = self._all_document_excerpts(brief, attachments)
        compact = self._compact_brief_for_ai(brief)
        user_prompt = (
            f"Project: {project.name}\n\n"
            f"User request:\n{content.strip() or '(files only)'}\n\n"
            f"Current study brief:\n{json.dumps(compact, ensure_ascii=False)[:6000]}\n\n"
            f"Document excerpts:\n{excerpt[:4000]}\n"
        )
        yield from chat_stream(
            system_prompt=self.THINKING_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=900,
        )

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
                chat=chat,
            )

        transcript_lines: list[str] = []
        for msg in history[-16:]:
            role = getattr(msg, "role", "user")
            content = str(getattr(msg, "content", "") or "")
            if len(content) > 800:
                content = content[:800] + "…"
            transcript_lines.append(f"{role.upper()}: {content}")

        siblings = self._sibling_study_summaries(project.id, chat.id)
        user_prompt = render_study_brief_user_prompt(
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
            document_excerpts=self._all_document_excerpts(brief, new_attachments),
            version_history_json=self._version_history_for_ai(chat.id),
        )
        return chat_json(system_prompt=STUDY_BRIEF_SYSTEM_PROMPT, user_prompt=user_prompt)

    def _heuristic_ai(
        self,
        brief: StudyBrief,
        user_message: str,
        new_attachments: list[AttachmentBrief],
        *,
        siblings: list[dict[str, Any]] | None = None,
        chat: Any | None = None,
    ) -> dict[str, Any]:
        """Deterministic fallback when OPENAI_API_KEY is missing (tests / local)."""
        text = user_message.strip()
        lower = text.lower()
        siblings = siblings or []
        brief = apply_inferred_audience(brief, text=text)
        if chat is not None:
            restored = self._heuristic_restore(chat, brief, text)
            if restored is not None:
                return restored
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

    _BRIEF_MERGE_KEYS = (
        "title",
        "background",
        "language",
        "study_type",
        "main_question",
        "orientation_text",
        "rating_scale",
        "categories",
        "classification_questions",
        "audience",
    )

    def _merge_brief_fields(
        self, current: StudyBrief, raw: dict[str, Any]
    ) -> StudyBrief:
        """Apply whatever AI fields validate, instead of dropping the whole draft."""
        data = current.model_dump(mode="json")
        for key in self._BRIEF_MERGE_KEYS:
            if key not in raw:
                continue
            trial = dict(data)
            trial[key] = raw[key]
            try:
                candidate = StudyBrief.model_validate(trial)
            except Exception:
                logger.warning("AI study_brief field %s failed validation; skipped", key)
                continue
            if key == "categories" and current.categories and not candidate.categories:
                continue
            if (
                key == "classification_questions"
                and current.classification_questions
                and not candidate.classification_questions
            ):
                continue
            data[key] = raw[key]
        return StudyBrief.model_validate(data)

    def _brief_from_ai(self, current: StudyBrief, payload: dict[str, Any]) -> StudyBrief:
        raw = payload.get("study_brief")
        if not isinstance(raw, dict):
            return current
        ready = current.status in {"ready", "created"} or is_brief_ready_for_review(
            current
        )
        if ready:
            incoming = self._merge_brief_fields(current, raw)
        else:
            try:
                incoming = StudyBrief.model_validate(raw)
            except Exception as exc:
                logger.warning(
                    "AI study_brief failed full validation; merging valid fields: %s",
                    exc,
                )
                incoming = self._merge_brief_fields(current, raw)
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
