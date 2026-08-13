"""Schemas for conversational study briefing and creation."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

StudyTypeLit = Literal["grid", "text"]
BriefPhase = Literal["gathering", "brief_ready", "creating", "created"]
BriefStatus = Literal["gathering", "ready", "confirmed", "created"]

# Canonical audience age segments (aligned with the Unilever create-study wizard).
AGE_SEGMENTS = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
_AGE_ALIASES = {
    "18-24": "18-24", "18 - 24": "18-24", "1824": "18-24",
    "25-34": "25-34", "25 - 34": "25-34", "2534": "25-34",
    "35-44": "35-44", "35 - 44": "35-44", "3544": "35-44",
    "45-54": "45-54", "45 - 54": "45-54", "4554": "45-54",
    "55-64": "55-64", "55 - 64": "55-64", "5564": "55-64",
    "65+": "65+", "65 +": "65+", "65plus": "65+", "65 and over": "65+", "65+ ": "65+",
}


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


# Text-study structure (statements instead of images). Grid keeps looser image-folder limits.
MIN_TEXT_CATEGORIES = 3
MAX_TEXT_CATEGORIES = 20
MIN_TEXT_STATEMENTS = 3
MAX_TEXT_STATEMENTS = 20
MAX_STATEMENT_CHARS = 150
# When the AI generates (or we pad an undersized brief), aim above the floor.
TEXT_GENERATE_CATEGORIES = 4
TEXT_GENERATE_STATEMENTS = 6
MIN_GRID_CATEGORIES = 2
MAX_GRID_CATEGORIES = 15
MIN_GRID_ELEMENTS = 2
MAX_GRID_ELEMENTS = 12


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
    # Present on the current AI turn only — not persisted on the chat brief.
    extracted_text: str | None = None


class AudienceBrief(BaseModel):
    """Respondent targeting aligned with the Unilever audience step."""

    number_of_respondents: int | None = Field(default=None, ge=1, le=1500)
    age_segments: list[str] = Field(default_factory=list)
    age_distribution: dict[str, int] = Field(default_factory=dict)
    countries: list[str] = Field(default_factory=list)
    gender_male: int = Field(default=50, ge=0, le=100)
    gender_female: int = Field(default=50, ge=0, le=100)

    @field_validator("age_segments")
    @classmethod
    def normalize_age_segments(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for raw in value:
            key = str(raw).strip().lower()
            canon = _AGE_ALIASES.get(key) or _AGE_ALIASES.get(key.replace(" ", ""))
            if canon is None and key.isdigit():
                age = int(key)
                if 18 <= age <= 24:
                    canon = "18-24"
                elif 25 <= age <= 34:
                    canon = "25-34"
                elif 35 <= age <= 44:
                    canon = "35-44"
                elif 45 <= age <= 54:
                    canon = "45-54"
                elif 55 <= age <= 64:
                    canon = "55-64"
                elif age >= 65:
                    canon = "65+"
            if canon and canon not in out:
                out.append(canon)
            elif raw in AGE_SEGMENTS and raw not in out:
                out.append(raw)
        return sorted(out, key=lambda s: AGE_SEGMENTS.index(s))

    @field_validator("countries")
    @classmethod
    def normalize_countries(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for c in value:
            name = str(c).strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name[:100])
        return out

    @model_validator(mode="after")
    def normalize_age_distribution(self) -> "AudienceBrief":
        normalized: dict[str, int] = {}
        for raw_key, raw_percent in self.age_distribution.items():
            key = str(raw_key).strip().lower()
            segment = _AGE_ALIASES.get(key) or _AGE_ALIASES.get(key.replace(" ", ""))
            if segment is None and key.isdigit():
                age = int(key)
                if 18 <= age <= 24:
                    segment = "18-24"
                elif 25 <= age <= 34:
                    segment = "25-34"
                elif 35 <= age <= 44:
                    segment = "35-44"
                elif 45 <= age <= 54:
                    segment = "45-54"
                elif 55 <= age <= 64:
                    segment = "55-64"
                elif age >= 65:
                    segment = "65+"
            if segment:
                normalized[segment] = normalized.get(segment, 0) + max(
                    0, min(100, int(raw_percent))
                )

        # Backward compatibility: older briefs only stored selected segments.
        if not normalized and self.age_segments:
            base, remainder = divmod(100, len(self.age_segments))
            normalized = {
                segment: base + (1 if index < remainder else 0)
                for index, segment in enumerate(self.age_segments)
            }

        self.age_distribution = {
            segment: normalized[segment]
            for segment in AGE_SEGMENTS
            if normalized.get(segment, 0) > 0
        }
        self.age_segments = list(self.age_distribution)
        return self


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
    audience: AudienceBrief = Field(default_factory=AudienceBrief)
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
    audience: AudienceBrief | None = None


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
    extracted_text: str | None = None
