"""Schemas for conversational study briefing and creation."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

StudyTypeLit = Literal["grid", "text"]
BriefPhase = Literal["gathering", "brief_ready", "creating", "created"]
BriefStatus = Literal["gathering", "ready", "confirmed", "created"]


class RatingScaleBrief(BaseModel):
    min_value: int = 1
    max_value: int = 5
    min_label: str = Field(default="Not at all", max_length=50)
    max_label: str = Field(default="Extremely", max_length=50)
    middle_label: str = Field(default="", max_length=50)


class ElementBrief(BaseModel):
    name: str = Field(default="", max_length=150)
    element_type: Literal["image", "text"] = "text"
    content: str = ""
    description: str = ""


class CategoryBrief(BaseModel):
    name: str = Field(default="", max_length=100)
    elements: list[ElementBrief] = Field(default_factory=list)


class ClassificationOptionBrief(BaseModel):
    id: str = Field(default="", max_length=10)
    text: str = Field(default="", max_length=200)


class ClassificationQuestionBrief(BaseModel):
    question_text: str = Field(default="", max_length=500)
    is_required: bool = True
    options: list[str] = Field(default_factory=list)

    @field_validator("options")
    @classmethod
    def normalize_options(cls, value: list[str]) -> list[str]:
        return [o.strip() for o in value if o and str(o).strip()]


class AttachmentBrief(BaseModel):
    url: str
    filename: str = ""
    content_type: str = ""
    size_bytes: int | None = None
    category: str | None = None
    relative_path: str | None = None


class StudyBrief(BaseModel):
    title: str = Field(default="", max_length=255)
    background: str = Field(default="", max_length=2000)
    language: str = Field(default="en", max_length=10)
    study_type: StudyTypeLit | None = None
    main_question: str = ""
    orientation_text: str = ""
    rating_scale: RatingScaleBrief = Field(default_factory=RatingScaleBrief)
    categories: list[CategoryBrief] = Field(default_factory=list)
    classification_questions: list[ClassificationQuestionBrief] = Field(default_factory=list)
    attachments: list[AttachmentBrief] = Field(default_factory=list)
    status: BriefStatus = "gathering"
    study_id: UUID | None = None
    missing_fields: list[str] = Field(default_factory=list)

    def merge_attachments(self, new_items: list[AttachmentBrief]) -> None:
        seen = {a.url for a in self.attachments}
        for item in new_items:
            if item.url and item.url not in seen:
                self.attachments.append(item)
                seen.add(item.url)


class AiTurnRequest(BaseModel):
    content: str = Field(default="", max_length=20_000)
    attachment_urls: list[str] = Field(default_factory=list)
    attachments: list[AttachmentBrief] = Field(default_factory=list)


class AiTurnResponse(BaseModel):
    user_message: dict[str, Any] | None = None
    assistant_message: dict[str, Any]
    phase: BriefPhase
    study_brief: StudyBrief
    suggested_chat_title: str | None = None
    continued: bool = True


class AiContinueEmptyResponse(BaseModel):
    continued: bool = False


class StudyBriefUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    background: str | None = Field(default=None, max_length=2000)
    language: str | None = Field(default=None, max_length=10)
    study_type: StudyTypeLit | None = None
    main_question: str | None = None
    orientation_text: str | None = None
    rating_scale: RatingScaleBrief | None = None
    categories: list[CategoryBrief] | None = None
    classification_questions: list[ClassificationQuestionBrief] | None = None


class StudyBriefOut(BaseModel):
    phase: BriefPhase
    study_brief: StudyBrief


class StudyConfirmResponse(BaseModel):
    study_id: UUID
    phase: BriefPhase
    study_brief: StudyBrief
    message: str


class UploadOut(BaseModel):
    url: str
    filename: str
    content_type: str
    size_bytes: int
    category: str | None = None
    relative_path: str | None = None
