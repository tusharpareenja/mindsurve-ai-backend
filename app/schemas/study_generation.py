"""Schemas for study task-generation orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

GenerationStatus = Literal[
    "queued",
    "generating",
    "saving",
    "ready",
    "failed",
    "cancelled",
    "launched",
]


class GenerationRunOut(BaseModel):
    id: UUID
    chat_id: UUID
    project_id: UUID
    study_id: UUID
    upstream_job_id: str | None = None
    revision: int
    status: GenerationStatus
    progress: float = Field(ge=0, le=100)
    message: str = ""
    error: str | None = None
    fingerprint: str = ""
    preview_url: str | None = None
    share_url: str | None = None
    study_status: str = "draft"
    websocket_url: str | None = None
    research_tip: str | None = None
    retryable: bool = False
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    launched_at: datetime | None = None


class GenerationStartResponse(BaseModel):
    run: GenerationRunOut
    resumed: bool = False


class GenerationLaunchResponse(BaseModel):
    run: GenerationRunOut
    share_url: str
    message: str


class BriefRegenerateRequest(BaseModel):
    """PATCH-like brief update that requires regeneration confirmation."""

    title: str | None = Field(default=None, max_length=255)
    background: str | None = Field(default=None, max_length=2000)
    language: str | None = Field(default=None, max_length=10)
    study_type: Literal["grid", "text"] | None = None
    main_question: str | None = None
    orientation_text: str | None = None
    rating_scale: dict[str, Any] | None = None
    categories: list[dict[str, Any]] | None = None
    classification_questions: list[dict[str, Any]] | None = None
    audience: dict[str, Any] | None = None
    confirm_regeneration: bool = False


class BriefChangePreview(BaseModel):
    requires_regeneration: bool
    changed_fields: list[str]
    message: str
