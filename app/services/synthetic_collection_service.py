"""Orchestrate Unilever synthetic respondent collection from MindSurve chats."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import AppError, NotFoundError
from app.db.models.synthetic_collection_run import SyntheticCollectionRun
from app.db.models.user import User
from app.repositories.project_repository import ProjectRepository
from app.repositories.synthetic_collection_repository import SyntheticCollectionRepository
from app.schemas.study_brief import StudyBrief
from app.schemas.synthetic_collection import (
    ResponseStatsOut,
    SyntheticCollectionRunOut,
    SyntheticCollectionStartResponse,
    SyntheticMode,
)
from app.services.folder_brief import apply_folder_categories
from app.services.study_brief_validator import apply_defaults
from app.services.synthetic_capacity import resolve_synthetic_respondent_count
from app.services.unilever_client import UnileverClient

logger = logging.getLogger(__name__)

_ACTIVE = {"queued", "running"}
_DONE_PROGRESS = re.compile(r"(\d+)\s*/\s*(\d+)")


class SyntheticCollectionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SyntheticCollectionRepository(db)
        self.projects = ProjectRepository(db)

    def start(
        self,
        user: User,
        chat_id: UUID,
        *,
        access_token: str,
        mode: SyntheticMode = "ai",
        randomize: bool | None = None,
    ) -> SyntheticCollectionStartResponse:
        chat, project, brief = self._owned_ready_chat(user, chat_id)
        study_id = brief.study_id
        if study_id is None:
            raise AppError(
                "Create and launch the study first, then start AI collection.",
                status_code=422,
            )

        use_randomize = bool(randomize) if randomize is not None else mode == "randomize"
        resolved_mode: SyntheticMode = "randomize" if use_randomize else "ai"

        active = self.repo.active_for_chat(chat_id)
        if active is not None:
            client = UnileverClient(access_token=access_token)
            self._refresh_from_upstream(active, client)
            self.db.commit()
            self.db.refresh(active)
            return SyntheticCollectionStartResponse(
                run=self._to_out(active, client),
                resumed=True,
            )

        latest = self.repo.latest_for_chat(chat_id)
        if latest and latest.status == "completed" and latest.mode == resolved_mode:
            client = UnileverClient(access_token=access_token)
            self._refresh_stats(latest, client)
            self.db.commit()
            self.db.refresh(latest)
            return SyntheticCollectionStartResponse(
                run=self._to_out(latest, client),
                resumed=True,
            )

        audience_requested = int(brief.audience.number_of_respondents or 0)
        to_run, max_ai = resolve_synthetic_respondent_count(
            brief, requested=audience_requested
        )
        if to_run <= 0:
            raise AppError(
                "AI collection needs screening questions with 2+ options each "
                "(capacity is the product of option counts, e.g. 5×2 = 32).",
                status_code=422,
            )

        if audience_requested > max_ai:
            start_message = (
                f"Starting synthetic collection for {to_run} of {audience_requested} "
                f"requested respondents (AI max from screeners is {max_ai})."
            )
        elif audience_requested > 0:
            start_message = (
                f"Starting synthetic collection for {to_run} respondents…"
            )
        else:
            start_message = (
                f"Starting synthetic collection for {to_run} respondents "
                f"(AI capacity from screeners)…"
            )

        client = UnileverClient(access_token=access_token)
        run = SyntheticCollectionRun(
            chat_id=chat.id,
            project_id=project.id,
            user_id=user.id,
            study_id=study_id,
            mode=resolved_mode,
            status="queued",
            progress=0.0,
            message=start_message,
            respondents_requested=to_run,
            respondents_completed=0,
            stats_json=self._empty_stats(to_run).model_dump(),
        )
        self.repo.add(run)
        self.db.flush()

        try:
            result = client.start_simulate_ai_respondents(
                study_id,
                randomize=use_randomize,
                max_respondents=to_run,
            )
        except AppError as exc:
            run.status = "failed"
            run.error = exc.message
            run.message = "We couldn’t start AI respondent collection."
            run.completed_at = datetime.now(UTC)
            self.repo.save(run)
            self.db.commit()
            self.db.refresh(run)
            raise

        job_id = result.get("job_id")
        if job_id:
            run.upstream_job_id = str(job_id)
            run.status = "running"
            run.progress = 1.0
            run.message = str(result.get("message") or "Collecting synthetic responses…")
            run.respondents_requested = int(
                result.get("respondents_requested") or requested or 0
            )
        elif result.get("success") is True or "respondents_simulated" in result:
            # Sync completion path
            completed = int(
                result.get("respondents_simulated")
                or result.get("respondents_requested")
                or requested
                or 0
            )
            run.status = "completed"
            run.progress = 100.0
            run.respondents_completed = completed
            run.respondents_requested = max(run.respondents_requested, completed)
            run.message = str(result.get("message") or "Synthetic collection completed.")
            run.completed_at = datetime.now(UTC)
            self._refresh_stats(run, client)
        else:
            run.status = "failed"
            run.error = "The study engine didn’t return a job id."
            run.message = "Synthetic collection didn’t start correctly."
            run.completed_at = datetime.now(UTC)

        self.repo.save(run)
        self.db.commit()
        self.db.refresh(run)
        return SyntheticCollectionStartResponse(
            run=self._to_out(run, client),
            resumed=False,
        )

    def get_status(
        self,
        user: User,
        chat_id: UUID,
        *,
        access_token: str,
        run_id: UUID | None = None,
    ) -> SyntheticCollectionRunOut:
        self._owned_chat(user, chat_id)
        run = self.repo.get(run_id) if run_id else self.repo.latest_for_chat(chat_id)
        if run is None or run.chat_id != chat_id:
            raise NotFoundError("No synthetic collection run found for this chat.")

        client = UnileverClient(access_token=access_token)
        if run.status in _ACTIVE and run.upstream_job_id:
            self._refresh_from_upstream(run, client)
        else:
            self._refresh_stats(run, client)
        self.db.commit()
        self.db.refresh(run)
        return self._to_out(run, client)

    def retry(
        self,
        user: User,
        chat_id: UUID,
        *,
        access_token: str,
        mode: SyntheticMode | None = None,
    ) -> SyntheticCollectionStartResponse:
        latest = self.repo.latest_for_chat(chat_id)
        if latest and latest.status in _ACTIVE:
            raise AppError(
                "Synthetic collection is already running. Please wait for it to finish.",
                status_code=409,
            )
        resolved: SyntheticMode = mode or (latest.mode if latest else "ai")  # type: ignore[assignment]
        if resolved not in {"ai", "randomize"}:
            resolved = "ai"
        return self.start(
            user,
            chat_id,
            access_token=access_token,
            mode=resolved,
            randomize=resolved == "randomize",
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

    def _owned_ready_chat(self, user: User, chat_id: UUID):
        chat, project = self._owned_chat(user, chat_id)
        raw = getattr(chat, "study_brief", None) or {}
        if not isinstance(raw, dict):
            raw = {}
        try:
            brief = apply_defaults(StudyBrief.model_validate(raw))
        except Exception:
            brief = apply_defaults(StudyBrief())
        brief = apply_folder_categories(brief, brief.attachments)
        return chat, project, brief

    def _refresh_from_upstream(
        self, run: SyntheticCollectionRun, client: UnileverClient
    ) -> None:
        if not run.upstream_job_id:
            return
        try:
            payload = client.simulate_job_status(run.upstream_job_id)
        except AppError as exc:
            logger.warning(
                "Failed to refresh simulate job %s: %s",
                run.upstream_job_id,
                exc.message,
            )
            return

        upstream = str(payload.get("status") or "").lower()
        progress = float(payload.get("progress") or run.progress or 0)
        message = str(payload.get("message") or run.message or "")
        requested = payload.get("respondents_requested")
        if isinstance(requested, int) and requested > 0:
            run.respondents_requested = requested

        run.progress = max(0.0, min(100.0, progress))
        if message:
            run.message = message
            match = _DONE_PROGRESS.search(message)
            if match:
                run.respondents_completed = int(match.group(1))
                run.respondents_requested = max(
                    run.respondents_requested, int(match.group(2))
                )

        if upstream in {"pending", "started"}:
            run.status = "queued" if upstream == "pending" else "running"
        elif upstream == "processing":
            run.status = "running"
        elif upstream == "completed":
            run.status = "completed"
            run.progress = 100.0
            run.respondents_completed = max(
                run.respondents_completed, run.respondents_requested
            )
            run.message = message or "Synthetic collection completed."
            run.completed_at = datetime.now(UTC)
            run.error = None
        elif upstream == "failed":
            run.status = "failed"
            run.error = str(payload.get("error") or "Synthetic collection failed.")
            run.message = "We couldn’t finish collecting synthetic responses."
            run.completed_at = datetime.now(UTC)
        elif upstream == "cancelled":
            run.status = "cancelled"
            run.message = "Synthetic collection was cancelled."
            run.completed_at = datetime.now(UTC)

        self._refresh_stats(run, client)
        self.repo.save(run)

    def _refresh_stats(
        self, run: SyntheticCollectionRun, client: UnileverClient
    ) -> None:
        try:
            analytics = client.get_study_analytics(run.study_id)
        except AppError:
            # Fall back to job progress counters.
            stats = self._stats_from_progress(run)
            run.stats_json = stats.model_dump()
            return

        completed = int(analytics.get("completed_responses") or 0)
        abandoned = int(analytics.get("abandoned_responses") or 0)
        in_progress = int(
            analytics.get("in_progress_responses")
            if analytics.get("in_progress_responses") is not None
            else 0
        )
        analytics_total = int(analytics.get("total_responses") or 0)
        # Prefer live job counters when analytics lag behind.
        if run.status in _ACTIVE and run.respondents_completed > completed:
            completed = run.respondents_completed
            remaining = max(0, run.respondents_requested - completed)
            in_progress = max(in_progress, 1 if remaining > 0 else 0)

        # Total = respondents who have started (completed + in progress + abandoned).
        started = completed + in_progress + abandoned
        if analytics_total > started:
            # Analytics may already report a combined started total.
            started = analytics_total
            if in_progress == 0 and started > completed + abandoned:
                in_progress = max(0, started - completed - abandoned)
        rate = (completed / started * 100) if started else 0.0
        avg = float(analytics.get("average_duration") or 0)

        run.stats_json = ResponseStatsOut(
            total=started,
            in_progress=in_progress,
            completed=completed,
            abandoned=abandoned,
            completion_rate=round(rate, 1),
            avg_duration_seconds=avg,
        ).model_dump()
        if completed > run.respondents_completed:
            run.respondents_completed = completed

    def _stats_from_progress(self, run: SyntheticCollectionRun) -> ResponseStatsOut:
        completed = max(0, run.respondents_completed)
        remaining = max(0, run.respondents_requested - completed)
        in_progress = 1 if run.status in _ACTIVE and remaining > 0 else 0
        abandoned = 0
        started = completed + in_progress + abandoned
        rate = (completed / started * 100) if started else 0.0
        return ResponseStatsOut(
            total=started,
            in_progress=in_progress,
            completed=completed,
            abandoned=abandoned,
            completion_rate=round(rate, 1),
            avg_duration_seconds=0.0,
        )

    @staticmethod
    def _empty_stats(requested: int) -> ResponseStatsOut:
        # Nobody has started yet — Total stays 0 until respondents begin.
        _ = requested
        return ResponseStatsOut(
            total=0,
            in_progress=0,
            completed=0,
            abandoned=0,
            completion_rate=0.0,
            avg_duration_seconds=0.0,
        )

    def _to_out(
        self, run: SyntheticCollectionRun, client: UnileverClient | None
    ) -> SyntheticCollectionRunOut:
        ws_url = None
        if client and run.upstream_job_id and run.status in _ACTIVE:
            ws_url = client.simulate_websocket_url(run.upstream_job_id)
        raw_stats = run.stats_json if isinstance(run.stats_json, dict) else {}
        try:
            stats = ResponseStatsOut.model_validate(raw_stats)
        except Exception:
            stats = self._stats_from_progress(run)
        return SyntheticCollectionRunOut(
            id=run.id,
            chat_id=run.chat_id,
            project_id=run.project_id,
            study_id=run.study_id,
            upstream_job_id=run.upstream_job_id,
            mode=run.mode if run.mode in {"ai", "randomize"} else "ai",  # type: ignore[arg-type]
            status=run.status,  # type: ignore[arg-type]
            progress=run.progress,
            message=run.message,
            error=run.error,
            respondents_requested=run.respondents_requested,
            respondents_completed=run.respondents_completed,
            stats=stats,
            websocket_url=ws_url,
            retryable=run.status in {"failed", "cancelled"},
            created_at=run.created_at,
            updated_at=run.updated_at,
            completed_at=run.completed_at,
        )
