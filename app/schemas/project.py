"""Pydantic schemas for projects, chats, and messages."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.project import Project


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class ProjectUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str = ""
    idea: str | None = None
    workflow_type: str = "beginner"
    status: str = "CREATED"
    is_inbox: bool = False
    is_owner: bool = True
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_project(cls, project: Project, *, viewer_id: UUID | None = None) -> ProjectOut:
        workflow = project.workflow_type or "beginner"
        is_owner = True if viewer_id is None else project.creator_id == viewer_id
        return cls(
            id=project.id,
            title=project.name,
            description=project.description or "",
            idea=project.idea,
            workflow_type=workflow,
            status=project.status or "CREATED",
            is_inbox=workflow == "inbox",
            is_owner=is_owner,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )


class CollaboratorInvite(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class CollaboratorOut(BaseModel):
    id: str
    email: str
    name: str | None = None
    is_owner: bool = False
    status: Literal["active", "pending"] = "active"


class CollaboratorInviteResult(BaseModel):
    id: str
    email: str
    status: Literal["active", "pending"]
    message: str = "Invitation sent."
    project_id: UUID | None = None
    chat_id: UUID | None = None
    promoted_from_inbox: bool = False


class ChatCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ChatStart(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class ChatUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    project_id: UUID | None = None


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_preview: str | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    role: Literal["user", "assistant", "system"] = "user"
    metadata: dict[str, Any] | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chat_id: UUID
    role: str
    content: str
    created_at: datetime
    metadata: dict[str, Any] | None = None

    @field_validator("metadata", mode="before")
    @classmethod
    def coerce_metadata(cls, value: object) -> object:
        return value


class MessagePageOut(BaseModel):
    items: list[MessageOut]
    has_more: bool
    next_before: str | None = None


class ChatStartOut(BaseModel):
    chat: ChatOut
    message: MessageOut


class MessageResponse(BaseModel):
    message: str
