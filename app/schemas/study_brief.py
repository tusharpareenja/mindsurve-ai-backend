"""Schemas for conversational study briefing and creation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

StudyTypeLit = Literal["grid", "text", "layer"]
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


def _clip(value: Any, max_len: int) -> str:
    return str(value or "")[:max_len]


def _coerce_option_texts(value: Any) -> list[str]:
    """Accept AI option objects `{text: ...}` as well as plain strings."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = str(
                item.get("text") or item.get("label") or item.get("value") or ""
            ).strip()
        elif item is None:
            text = ""
        else:
            text = str(item).strip()
        if text:
            out.append(text[:200])
    return out


class RatingScaleBrief(BaseModel):
    min_value: int = 1
    max_value: int = 5
    min_label: str = Field(default="Not at all", max_length=50)
    max_label: str = Field(default="Extremely", max_length=50)
    middle_label: str = Field(default="", max_length=50)

    @model_validator(mode="before")
    @classmethod
    def clip_labels(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key in ("min_label", "max_label", "middle_label"):
            if key in data:
                data[key] = _clip(data[key], 50)
        return data


class ElementBrief(BaseModel):
    name: str = Field(default="", max_length=150)
    element_type: Literal["image", "text"] = "text"
    content: str = ""
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def coerce_element(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "name" in data:
            data["name"] = _clip(data["name"], 150)
        raw_type = str(data.get("element_type") or "").strip().lower()
        if raw_type in {"image", "img", "visual", "photo"}:
            data["element_type"] = "image"
        elif raw_type:
            data["element_type"] = "text"
        return data


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
MIN_LAYER_LAYERS = 3
MAX_LAYER_LAYERS = 15
MIN_LAYER_ELEMENTS = 3
MAX_LAYER_ELEMENTS = 30

DEFAULT_LAYER_TRANSFORM = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}


def _coerce_elements(value: Any) -> list[Any]:
    """Accept AI aliases: statements/items/messages, or a list of strings."""
    if not isinstance(value, list):
        return []
    out: list[Any] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(
                    {"name": text[:150], "element_type": "text", "content": text}
                )
            continue
        if item:
            out.append(item)
    return out


class CategoryBrief(BaseModel):
    name: str = Field(default="", max_length=100)
    elements: list[ElementBrief] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def clip_name(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"name": _clip(data, 100), "elements": []}
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "name" in data:
            data["name"] = _clip(data["name"], 100)
        if not data.get("elements"):
            for alias in ("statements", "items", "messages", "lines"):
                if alias in data:
                    data["elements"] = data[alias]
                    break
        if "elements" in data:
            data["elements"] = _coerce_elements(data["elements"])
        return data


class LayerTransformBrief(BaseModel):
    x: float = 0.0
    y: float = 0.0
    width: float = 100.0
    height: float = 100.0

    @model_validator(mode="before")
    @classmethod
    def clamp_values(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return DEFAULT_LAYER_TRANSFORM.copy()
        out = dict(DEFAULT_LAYER_TRANSFORM)
        for key in ("x", "y", "width", "height"):
            raw = data.get(key, out[key])
            try:
                val = float(raw)
            except (TypeError, ValueError):
                val = out[key]
            out[key] = max(0.0, min(100.0, val))
        return out


class LayerElementBrief(BaseModel):
    name: str = Field(default="", max_length=150)
    content: str = ""
    order: int = 0
    transform: LayerTransformBrief = Field(
        default_factory=lambda: LayerTransformBrief(**DEFAULT_LAYER_TRANSFORM)
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_element(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {
                "name": _clip(data, 150),
                "content": data,
                "order": 0,
                "transform": DEFAULT_LAYER_TRANSFORM.copy(),
            }
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "name" in data:
            data["name"] = _clip(data["name"], 150)
        if not data.get("content") and data.get("url"):
            data["content"] = data["url"]
        if "transform" not in data:
            data["transform"] = DEFAULT_LAYER_TRANSFORM.copy()
        return data


class LayerBrief(BaseModel):
    name: str = Field(default="", max_length=100)
    z_index: int = 0
    order: int = 0
    elements: list[LayerElementBrief] = Field(default_factory=list)
    transform: LayerTransformBrief = Field(
        default_factory=lambda: LayerTransformBrief(**DEFAULT_LAYER_TRANSFORM)
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_layer(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {
                "name": _clip(data, 100),
                "elements": [],
                "z_index": 0,
                "order": 0,
                "transform": DEFAULT_LAYER_TRANSFORM.copy(),
            }
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "name" in data:
            data["name"] = _clip(data["name"], 100)
        if "z_index" not in data and "order" in data:
            data["z_index"] = data["order"]
        if "order" not in data and "z_index" in data:
            data["order"] = data["z_index"]
        if "transform" not in data:
            data["transform"] = DEFAULT_LAYER_TRANSFORM.copy()
        if not data.get("elements"):
            for alias in ("images", "items"):
                if alias in data:
                    data["elements"] = data[alias]
                    break
        return data


class ClassificationOptionBrief(BaseModel):
    id: str = Field(default="", max_length=10)
    text: str = Field(default="", max_length=200)


class ClassificationQuestionBrief(BaseModel):
    question_text: str = Field(default="", max_length=500)
    is_required: bool = True
    options: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def coerce_question(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"question_text": _clip(data, 500), "options": []}
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not data.get("question_text"):
            for alias in ("question", "text", "prompt", "label"):
                if data.get(alias):
                    data["question_text"] = data[alias]
                    break
        if "question_text" in data:
            data["question_text"] = _clip(data["question_text"], 500)
        if not data.get("options"):
            for alias in ("choices", "answers", "answer_options"):
                if alias in data:
                    data["options"] = data[alias]
                    break
        if "options" in data:
            data["options"] = _coerce_option_texts(data["options"])
        return data

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
    is_background: bool = False
    layer_order: int | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_flags(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        raw_bg = data.get("is_background")
        if isinstance(raw_bg, str):
            data["is_background"] = raw_bg.strip().lower() in {"1", "true", "yes", "y"}
        elif raw_bg is None:
            data["is_background"] = False
        else:
            data["is_background"] = bool(raw_bg)
        raw_order = data.get("layer_order")
        if raw_order in ("", "null", "None", None):
            data["layer_order"] = None
        elif isinstance(raw_order, str) and raw_order.strip().isdigit():
            data["layer_order"] = int(raw_order.strip())
        elif isinstance(raw_order, (int, float)):
            data["layer_order"] = int(raw_order)
        return data


class AudienceBrief(BaseModel):
    """Respondent targeting aligned with the Unilever audience step."""

    number_of_respondents: int | None = Field(default=None, ge=1, le=1500)
    age_segments: list[str] = Field(default_factory=list)
    age_distribution: dict[str, int] = Field(default_factory=dict)
    countries: list[str] = Field(default_factory=list)
    gender_male: int = Field(default=50, ge=0, le=100)
    gender_female: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def coerce_audience(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        raw_n = data.get("number_of_respondents")
        if raw_n in ("", "null", "None", None):
            data["number_of_respondents"] = None
        elif isinstance(raw_n, str):
            digits = "".join(ch for ch in raw_n if ch.isdigit())
            data["number_of_respondents"] = int(digits) if digits else None
        return data

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


_STUDY_TYPE_ALIASES = {
    "grid": "grid",
    "image": "grid",
    "images": "grid",
    "visual": "grid",
    "logo": "grid",
    "packaging": "grid",
    "text": "text",
    "statement": "text",
    "statements": "text",
    "copy": "text",
    "message": "text",
    "messages": "text",
    "layer": "layer",
    "layers": "layer",
    "layered": "layer",
    "pack shot": "layer",
    "packshot": "layer",
    "composite": "layer",
}


class StudyBrief(BaseModel):
    title: str = Field(default="", max_length=255)
    background: str = Field(default="", max_length=2000)
    language: str = Field(default="en", max_length=10)
    study_type: StudyTypeLit | None = None
    main_question: str = ""
    orientation_text: str = ""
    rating_scale: RatingScaleBrief = Field(default_factory=RatingScaleBrief)
    categories: list[CategoryBrief] = Field(default_factory=list)
    layers: list[LayerBrief] = Field(default_factory=list)
    background_image_url: str | None = None
    classification_questions: list[ClassificationQuestionBrief] = Field(default_factory=list)
    audience: AudienceBrief = Field(default_factory=AudienceBrief)
    attachments: list[AttachmentBrief] = Field(default_factory=list)
    status: BriefStatus = "gathering"
    study_id: UUID | None = None
    missing_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def coerce_ai_payload(cls, data: Any) -> Any:
        """Make common LLM JSON mistakes valid instead of dropping the whole brief."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "title" in data:
            data["title"] = _clip(data["title"], 255)
        if "background" in data:
            data["background"] = _clip(data["background"], 2000)
        if "language" in data:
            data["language"] = _clip(data["language"], 10) or "en"

        raw_type = data.get("study_type")
        if raw_type is None or raw_type == "":
            data["study_type"] = None
        elif isinstance(raw_type, str):
            data["study_type"] = _STUDY_TYPE_ALIASES.get(raw_type.strip().lower())

        raw_status = data.get("status")
        if raw_status not in {"gathering", "ready", "confirmed", "created"}:
            data["status"] = "gathering"

        study_id = data.get("study_id")
        if study_id in ("", "null", "None", None):
            data["study_id"] = None
        elif isinstance(study_id, str):
            try:
                UUID(study_id)
            except ValueError:
                data["study_id"] = None

        attachments = data.get("attachments")
        if isinstance(attachments, list):
            data["attachments"] = [
                item
                for item in attachments
                if isinstance(item, dict) and str(item.get("url") or "").strip()
            ]

        categories = data.get("categories")
        if isinstance(categories, list):
            data["categories"] = [
                {"name": item, "elements": []} if isinstance(item, str) else item
                for item in categories
                if item
            ]

        layers = data.get("layers")
        if isinstance(layers, list):
            data["layers"] = [
                {"name": item, "elements": []} if isinstance(item, str) else item
                for item in layers
                if item
            ]
        bg_url = data.get("background_image_url")
        if bg_url in ("", "null", "None", None):
            data["background_image_url"] = None

        questions = data.get("classification_questions")
        if isinstance(questions, list):
            data["classification_questions"] = [
                {"question_text": item, "options": []}
                if isinstance(item, str)
                else item
                for item in questions
                if item
            ]

        return data

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


class BriefVersionMeta(BaseModel):
    version: int
    total: int
    summary: str = ""
    source: str = "ai"
    changed_fields: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class BriefVersionOut(BaseModel):
    version: int
    summary: str = ""
    source: str = "ai"
    changed_fields: list[str] = Field(default_factory=list)
    created_at: datetime
    study_brief: StudyBrief


class BriefVersionListOut(BaseModel):
    current_version: int
    total: int
    versions: list[BriefVersionOut]


class AiTurnResponse(BaseModel):
    user_message: dict[str, Any] | None = None
    assistant_message: dict[str, Any]
    phase: BriefPhase
    study_brief: StudyBrief
    suggested_chat_title: str | None = None
    continued: bool = True
    version: BriefVersionMeta | None = None
    changed_fields: list[str] = Field(default_factory=list)


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
    version: BriefVersionMeta | None = None


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
    is_background: bool = False
    layer_order: int | None = None
