"""Orchestrate Unilever task generation from MindSurve chats."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError, NotFoundError
from app.db.models.study_generation_run import StudyGenerationRun
from app.db.models.user import User
from app.repositories.project_repository import ProjectRepository
from app.repositories.study_generation_repository import StudyGenerationRepository
from app.schemas.study_brief import StudyBrief, StudyBriefUpdate
from app.schemas.study_generation import (
    BriefChangePreview,
    GenerationLaunchResponse,
    GenerationRunOut,
    GenerationStartResponse,
)
from app.services.folder_brief import apply_folder_categories
from app.services.study_brief_validator import apply_defaults, compute_missing_fields
from app.services.study_payload import (
    build_generate_tasks_payload,
    diff_task_affecting,
    fingerprint_brief,
    research_tip_for_progress,
)
from app.services.unilever_client import UnileverClient

logger = logging.getLogger(__name__)

_ACTIVE = {"queued", "generating", "saving"}
_TERMINAL_OK = {"ready", "launched"}


class StudyGenerationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = StudyGenerationRepository(db)
        self.projects = ProjectRepository(db)

    def start(
        self,
        user: User,
        chat_id: UUID,
        *,
        access_token: str,
        force_new: bool = False,
    ) -> GenerationStartResponse:
        chat, project, brief = self._owned_ready_chat(user, chat_id)
        study_id = brief.study_id
        if study_id is None:
            raise AppError(
                "Create the study draft first (Continue with study), then generate tasks.",
                status_code=422,
            )

        if not force_new:
            active = self.repo.active_for_chat(chat_id)
            if active is not None:
                client = UnileverClient(access_token=access_token)
                self._refresh_from_upstream(active, client)
                self.db.commit()
                self.db.refresh(active)
                return GenerationStartResponse(
                    run=self._to_out(active, client),
                    resumed=True,
                )

            latest = self.repo.latest_for_chat(chat_id)
            if latest and (
                latest.status == "launched" or latest.study_status == "active"
            ):
                client = UnileverClient(access_token=access_token)
                return GenerationStartResponse(
                    run=self._to_out(latest, client),
                    resumed=True,
                )
            if (
                latest
                and latest.status in _TERMINAL_OK
                and latest.fingerprint == fingerprint_brief(brief)
            ):
                client = UnileverClient(access_token=access_token)
                return GenerationStartResponse(
                    run=self._to_out(latest, client),
                    resumed=True,
                )

        latest = self.repo.latest_for_chat(chat_id)
        if latest and (latest.status == "launched" or latest.study_status == "active"):
            raise AppError(
                "This study is already live. Create a new study to change the design.",
                status_code=409,
            )

        client = UnileverClient(access_token=access_token)
        payload = build_generate_tasks_payload(brief, study_id)
        fingerprint = fingerprint_brief(brief)
        revision = self.repo.max_revision(chat_id) + 1

        run = StudyGenerationRun(
            chat_id=chat.id,
            project_id=project.id,
            user_id=user.id,
            study_id=study_id,
            revision=revision,
            status="queued",
            progress=0.0,
            message="Starting task generation…",
            fingerprint=fingerprint,
            preview_url=self._preview_url(study_id),
            share_url=None,
            study_status="draft",
            snapshot_json={"brief_fingerprint": fingerprint},
        )
        self.repo.add(run)
        self.db.flush()

        try:
            result = client.generate_tasks(payload)
        except AppError as exc:
            run.status = "failed"
            run.error = exc.message
            run.message = "We couldn’t start task generation."
            run.completed_at = datetime.now(UTC)
            self.repo.save(run)
            self.db.commit()
            self.db.refresh(run)
            raise

        meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        job_id = meta.get("job_id")
        tasks = result.get("tasks") if isinstance(result.get("tasks"), dict) else {}

        if job_id:
            run.upstream_job_id = str(job_id)
            run.status = "generating"
            run.progress = 5.0
            run.message = str(meta.get("message") or "Task generation is running…")
        elif tasks:
            # Sync completion path
            run.status = "ready"
            run.progress = 100.0
            run.message = "Tasks are ready. Preview your study, then launch when you’re happy."
            run.completed_at = datetime.now(UTC)
            run.preview_url = self._preview_url(study_id)
        else:
            run.status = "failed"
            run.error = "The study engine didn’t return a job id or tasks."
            run.message = "Task generation didn’t start correctly."
            run.completed_at = datetime.now(UTC)

        self.repo.save(run)
        self.db.commit()
        self.db.refresh(run)
        return GenerationStartResponse(run=self._to_out(run, client), resumed=False)

    def get_status(
        self,
        user: User,
        chat_id: UUID,
        *,
        access_token: str,
        run_id: UUID | None = None,
    ) -> GenerationRunOut:
        self._owned_chat(user, chat_id)
        run = self.repo.get(run_id) if run_id else self.repo.latest_for_chat(chat_id)
        if run is None or run.chat_id != chat_id:
            raise NotFoundError("No task generation run found for this chat.")

        client = UnileverClient(access_token=access_token)
        if run.status in _ACTIVE and run.upstream_job_id:
            self._refresh_from_upstream(run, client)
            self.db.commit()
            self.db.refresh(run)
        return self._to_out(run, client)

    def retry(
        self,
        user: User,
        chat_id: UUID,
        *,
        access_token: str,
    ) -> GenerationStartResponse:
        self._owned_chat(user, chat_id)
        latest = self.repo.latest_for_chat(chat_id)
        if latest and latest.status in _ACTIVE:
            raise AppError(
                "Task generation is already running. Please wait for it to finish.",
                status_code=409,
            )
        return self.start(user, chat_id, access_token=access_token, force_new=True)

    def preview_brief_changes(
        self,
        user: User,
        chat_id: UUID,
        patch: StudyBriefUpdate,
    ) -> BriefChangePreview:
        _chat, _project, brief = self._owned_chat_brief(user, chat_id)
        latest = self.repo.latest_for_chat(chat_id)
        if latest and latest.study_status == "active":
            return BriefChangePreview(
                requires_regeneration=False,
                changed_fields=[],
                message="This study is already live. Task-affecting edits are locked.",
            )

        merged = self._merge_brief(brief, patch)
        changed = diff_task_affecting(brief, merged)
        if not changed:
            return BriefChangePreview(
                requires_regeneration=False,
                changed_fields=[],
                message="No task-affecting fields changed.",
            )

        needs = bool(
            latest
            and latest.status
            in {"ready", "queued", "generating", "saving", "failed", "cancelled", "launched"}
        )
        return BriefChangePreview(
            requires_regeneration=needs,
            changed_fields=changed,
            message=(
                "These changes have not been applied yet. Applying them requires "
                "replacing the previous task matrix and regenerating the study tasks."
                if needs
                else "These changes can be saved without regenerating tasks."
            ),
        )

    def apply_brief_and_regenerate(
        self,
        user: User,
        chat_id: UUID,
        patch: StudyBriefUpdate,
        *,
        access_token: str,
        confirm_regeneration: bool,
    ) -> GenerationStartResponse:
        chat, _project, brief = self._owned_chat_brief(user, chat_id)
        latest = self.repo.latest_for_chat(chat_id)
        if latest and latest.study_status == "active":
            raise AppError(
                "This study is already live. Create a new study to change the design.",
                status_code=409,
            )

        merged = self._merge_brief(brief, patch)
        changed = diff_task_affecting(brief, merged)
        needs_regen = bool(changed) and latest is not None and latest.status in {
            "ready",
            "queued",
            "generating",
            "saving",
            "failed",
            "cancelled",
            "launched",
        }
        # Always regen if tasks were previously ready/generated for this chat.
        if latest and latest.status == "ready" and changed:
            needs_regen = True
        if latest and latest.upstream_job_id and changed:
            needs_regen = True

        if needs_regen and not confirm_regeneration:
            raise AppError(
                "These changes require regenerating tasks. Confirm to continue.",
                status_code=409,
            )

        missing = compute_missing_fields(merged, require_grid_images=True)
        if missing:
            raise AppError(
                "Please complete the study brief before regenerating. Missing: "
                + ", ".join(missing[:8]),
                status_code=422,
            )

        chat.study_brief = merged.model_dump(mode="json")
        self.projects.save_chat(chat)
        self.db.commit()

        if needs_regen or (changed and brief.study_id):
            return self.start(user, chat_id, access_token=access_token, force_new=True)

        client = UnileverClient(access_token=access_token)
        run = latest or StudyGenerationRun(
            chat_id=chat_id,
            project_id=chat.project_id,
            user_id=user.id,
            study_id=merged.study_id or UUID(int=0),
            revision=1,
            status="ready",
            progress=100,
            message="Brief updated.",
            fingerprint=fingerprint_brief(merged),
        )
        return GenerationStartResponse(run=self._to_out(run, client), resumed=True)

    def launch(
        self,
        user: User,
        chat_id: UUID,
        *,
        access_token: str,
    ) -> GenerationLaunchResponse:
        _chat, _project, brief = self._owned_ready_chat(user, chat_id)
        run = self.repo.latest_for_chat(chat_id)
        if run is None:
            raise AppError(
                "Generate tasks first, then launch the study.",
                status_code=422,
            )
        if run.status in _ACTIVE:
            raise AppError(
                "Task generation is still running. Please wait until it’s finished.",
                status_code=409,
            )
        if run.status == "failed":
            raise AppError(
                "Task generation failed. Retry generation before launching.",
                status_code=409,
            )
        if run.status == "launched" or run.study_status == "active":
            client = UnileverClient(access_token=access_token)
            return GenerationLaunchResponse(
                run=self._to_out(run, client),
                share_url=run.share_url or self._share_url(run.study_id),
                message="Study is already live.",
            )

        current_fp = fingerprint_brief(brief)
        if run.fingerprint and run.fingerprint != current_fp:
            raise AppError(
                "Your study brief changed after tasks were generated. "
                "Regenerate tasks before launching.",
                status_code=409,
            )
        if run.status != "ready":
            raise AppError(
                "Tasks aren’t ready yet. Wait for generation to finish, then launch.",
                status_code=422,
            )

        client = UnileverClient(access_token=access_token)
        try:
            launched = client.launch_study(run.study_id)
        except AppError:
            raise

        share_url = (
            str(launched.get("share_url") or "").strip()
            or run.share_url
            or self._share_url(run.study_id)
        )
        run.status = "launched"
        run.study_status = "active"
        run.share_url = share_url
        run.launched_at = datetime.now(UTC)
        run.progress = 100.0
        run.message = "Your study is live. Share the participant link to collect responses."
        self.repo.save(run)

        # Reflect launch on the chat brief snapshot.
        brief.status = "created"
        brief.study_id = run.study_id
        chat = self.projects.get_chat_for_user(chat_id, user.id)
        if chat is not None:
            data = brief.model_dump(mode="json")
            data["launched"] = True
            chat.study_brief = data
            self.projects.save_chat(chat)

        self.db.commit()
        self.db.refresh(run)
        return GenerationLaunchResponse(
            run=self._to_out(run, client),
            share_url=share_url,
            message="Study launched successfully.",
        )

    # ── internals ─────────────────────────────────────────────────────────

    def _owned_chat(self, user: User, chat_id: UUID):
        chat = self.projects.get_chat_for_user(chat_id, user.id)
        if chat is None:
            raise NotFoundError("Chat not found.")
        project = self.projects.get_project_for_user(chat.project_id, user.id)
        if project is None:
            raise NotFoundError("Project not found.")
        return chat, project

    def _owned_chat_brief(self, user: User, chat_id: UUID):
        chat, project = self._owned_chat(user, chat_id)
        raw = getattr(chat, "study_brief", None) or {}
        if not isinstance(raw, dict):
            raw = {}
        try:
            brief = apply_defaults(StudyBrief.model_validate(raw))
        except Exception:
            brief = apply_defaults(StudyBrief())
        return chat, project, brief

    def _owned_ready_chat(self, user: User, chat_id: UUID):
        chat, project, brief = self._owned_chat_brief(user, chat_id)
        brief = apply_folder_categories(brief, brief.attachments)
        brief = apply_defaults(brief)
        return chat, project, brief

    def _merge_brief(self, brief: StudyBrief, patch: StudyBriefUpdate) -> StudyBrief:
        data = patch.model_dump(exclude_unset=True)
        base = brief.model_dump(mode="json")
        base.update(data)
        try:
            merged = StudyBrief.model_validate(base)
        except Exception as exc:
            raise AppError(
                "Those changes weren’t valid. Please review and try again.",
                status_code=422,
            ) from exc
        merged.study_id = brief.study_id
        if brief.status == "created":
            merged.status = "created"
        return apply_defaults(merged)

    def _refresh_from_upstream(self, run: StudyGenerationRun, client: UnileverClient) -> None:
        if not run.upstream_job_id:
            return
        try:
            status_payload = client.job_status(run.upstream_job_id)
        except AppError as exc:
            logger.warning("Failed to refresh job %s: %s", run.upstream_job_id, exc.message)
            return

        upstream_status = str(status_payload.get("status") or "").lower()
        progress = float(status_payload.get("progress") or run.progress or 0)
        message = str(status_payload.get("message") or run.message or "")

        run.progress = max(0.0, min(100.0, progress))
        if message:
            run.message = message

        if upstream_status in {"pending", "started"}:
            run.status = "queued" if upstream_status == "pending" else "generating"
        elif upstream_status == "processing":
            run.status = "saving" if progress >= 90 else "generating"
        elif upstream_status == "completed":
            try:
                client.job_result(run.upstream_job_id)
            except AppError:
                # Result fetch is best-effort; tasks may already be saved.
                pass
            run.status = "ready"
            run.progress = 100.0
            run.message = "Tasks are ready. Preview your study, then launch when you’re happy."
            run.completed_at = datetime.now(UTC)
            run.preview_url = self._preview_url(run.study_id)
            run.error = None
        elif upstream_status == "failed":
            run.status = "failed"
            run.error = str(status_payload.get("error") or "Task generation failed.")
            run.message = "We couldn’t finish generating tasks. You can retry."
            run.completed_at = datetime.now(UTC)
        elif upstream_status == "cancelled":
            run.status = "cancelled"
            run.message = "Task generation was cancelled."
            run.completed_at = datetime.now(UTC)

        self.repo.save(run)

    def _to_out(self, run: StudyGenerationRun, client: UnileverClient | None) -> GenerationRunOut:
        ws_url = None
        if client and run.upstream_job_id and run.status in _ACTIVE:
            ws_url = client.job_websocket_url(run.upstream_job_id)
        return GenerationRunOut(
            id=run.id,
            chat_id=run.chat_id,
            project_id=run.project_id,
            study_id=run.study_id,
            upstream_job_id=run.upstream_job_id,
            revision=run.revision,
            status=run.status,  # type: ignore[arg-type]
            progress=run.progress,
            message=run.message,
            error=run.error,
            fingerprint=run.fingerprint,
            preview_url=run.preview_url or self._preview_url(run.study_id),
            share_url=run.share_url,
            study_status=run.study_status,
            websocket_url=ws_url,
            research_tip=research_tip_for_progress(run.progress),
            retryable=run.status in {"failed", "cancelled"},
            created_at=run.created_at,
            updated_at=run.updated_at,
            completed_at=run.completed_at,
            launched_at=run.launched_at,
        )

    @staticmethod
    def _preview_url(study_id: UUID) -> str:
        settings = get_settings()
        base = (settings.STUDY_PREVIEW_BASE_URL or "").rstrip("/")
        return f"{base}?studyId={study_id}"

    @staticmethod
    def _share_url(study_id: UUID) -> str:
        settings = get_settings()
        base = (settings.STUDY_SHARE_BASE_URL or "https://mindsurve.com").rstrip("/")
        return f"{base}/participate/{study_id}"
