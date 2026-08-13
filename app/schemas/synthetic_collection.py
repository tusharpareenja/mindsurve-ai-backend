"""Schemas for synthetic respondent collection orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

SyntheticMode = Literal["ai", "randomize"]
SyntheticStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
]


class ResponseStatsOut(BaseModel):
    total: int = 0
    in_progress: int = 0
    completed: int = 0
    abandoned: int = 0
    completion_rate: float = 0.0
    avg_duration_seconds: float = 0.0


class SyntheticCollectionStartRequest(BaseModel):
    mode: SyntheticMode = "ai"
    # Temporary: AI vs randomize choice — may be removed later.
    randomize: bool | None = None


class SyntheticCollectionRunOut(BaseModel):
    id: UUID
    chat_id: UUID
    project_id: UUID
    study_id: UUID
    upstream_job_id: str | None = None
    mode: SyntheticMode
    status: SyntheticStatus
    progress: float = Field(ge=0, le=100)
    message: str = ""
    error: str | None = None
    respondents_requested: int = 0
    respondents_completed: int = 0
    stats: ResponseStatsOut
    websocket_url: str | None = None
    retryable: bool = False
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class SyntheticCollectionStartResponse(BaseModel):
    run: SyntheticCollectionRunOut
    resumed: bool = False
