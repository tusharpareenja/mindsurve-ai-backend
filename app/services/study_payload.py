"""Build Unilever GenerateTasksRequest payloads and task-affecting fingerprints."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_URL

from app.schemas.study_brief import StudyBrief

# Stable namespace so category/element IDs are reproducible for a study.
_MS_NAMESPACE = uuid5(NAMESPACE_URL, "mindsurve.ai/study-generation")

_AGE_LABELS = {
    "18-24": "18 - 24",
    "25-34": "25 - 34",
    "35-44": "35 - 44",
    "45-54": "45 - 54",
    "55-64": "55 - 64",
    "65+": "65+",
}


def audience_segmentation(brief: StudyBrief) -> dict[str, Any]:
    """Map the brief audience onto the Unilever `audience_segmentation` shape."""
    aud = brief.audience
    age_distribution = {
        _AGE_LABELS.get(segment, segment): percent
        for segment, percent in aud.age_distribution.items()
    }
    countries = aud.countries or ["United States"]
    return {
        "number_of_respondents": aud.number_of_respondents or 0,
        "country": ", ".join(countries),
        "gender_distribution": {
            "male": aud.gender_male,
            "female": aud.gender_female,
        },
        "age_distribution": age_distribution,
    }


def category_uuid(study_id: UUID, category_name: str) -> UUID:
    return uuid5(_MS_NAMESPACE, f"{study_id}:cat:{category_name.strip().lower()}")


def element_uuid(study_id: UUID, category_name: str, element_name: str) -> UUID:
    return uuid5(
        _MS_NAMESPACE,
        f"{study_id}:el:{category_name.strip().lower()}:{element_name.strip().lower()}",
    )


def build_generate_tasks_payload(brief: StudyBrief, study_id: UUID) -> dict[str, Any]:
    """Map MindSurve StudyBrief → Unilever GenerateTasksRequest JSON."""
    if brief.study_type not in {"grid", "text"}:
        raise ValueError("study_type must be grid or text")

    categories: list[dict[str, Any]] = []
    elements: list[dict[str, Any]] = []
    for order, cat in enumerate(brief.categories):
        cat_id = category_uuid(study_id, cat.name)
        categories.append(
            {
                "category_id": str(cat_id),
                "name": cat.name.strip()[:100],
                "order": order,
                "phase_type": brief.study_type,
            }
        )
        for el in cat.elements:
            el_id = element_uuid(study_id, cat.name, el.name)
            content = (el.content or el.name).strip()
            element_type = el.element_type if el.element_type in {"image", "text"} else (
                "text" if brief.study_type == "text" else "image"
            )
            elements.append(
                {
                    "element_id": str(el_id),
                    "name": el.name.strip()[:1000],
                    "description": el.description or None,
                    "element_type": element_type,
                    "content": content,
                    "alt_text": el.name.strip()[:200] or None,
                    "category_id": str(cat_id),
                }
            )

    classification_questions: list[dict[str, Any]] = []
    for q_order, q in enumerate(brief.classification_questions, start=1):
        options = [
            {
                "id": chr(ord("A") + opt_i) if opt_i < 26 else str(opt_i),
                "text": opt_text[:200],
                "order": opt_i + 1,
            }
            for opt_i, opt_text in enumerate(q.options)
        ]
        classification_questions.append(
            {
                "question_id": f"Q{q_order}"[:10],
                "question_text": q.question_text.strip()[:500],
                "question_type": "multiple_choice",
                "is_required": q.is_required,
                "order": q_order,
                "answer_options": options,
                "optional_classification_question": False,
                "config": {},
            }
        )

    audience = audience_segmentation(brief)
    rating = brief.rating_scale.model_dump()

    return {
        "study_id": str(study_id),
        "last_step": 6,
        "study_type": brief.study_type,
        "audience_segmentation": audience,
        "title": brief.title.strip()[:255],
        "background": brief.background.strip(),
        "language": (brief.language or "en")[:10],
        "main_question": brief.main_question.strip(),
        "orientation_text": brief.orientation_text.strip(),
        "rating_scale": rating,
        "classification_questions": classification_questions,
        "categories": categories,
        "elements": elements,
        "tasks_per_respondent": 0,
        "toggle_shuffle": False,
    }


def brief_change_snapshot(brief: StudyBrief) -> dict[str, Any]:
    """Canonical editable brief fields used for change summaries and versions."""
    return {
        "title": brief.title.strip(),
        "background": brief.background.strip(),
        "language": (brief.language or "en").strip().lower(),
        "study_type": brief.study_type,
        "main_question": brief.main_question.strip(),
        "orientation_text": brief.orientation_text.strip(),
        "rating_scale": brief.rating_scale.model_dump(mode="json"),
        "categories": [
            {
                "name": c.name.strip(),
                "elements": [
                    {
                        "name": e.name.strip(),
                        "element_type": e.element_type,
                        "content": (e.content or "").strip(),
                        "description": (e.description or "").strip(),
                    }
                    for e in c.elements
                ],
            }
            for c in brief.categories
        ],
        "classification_questions": [
            {
                "question_text": q.question_text.strip(),
                "is_required": q.is_required,
                "options": [o.strip() for o in q.options],
            }
            for q in brief.classification_questions
        ],
        "audience": {
            "number_of_respondents": brief.audience.number_of_respondents,
            "age_distribution": brief.audience.age_distribution,
            "countries": brief.audience.countries,
            "gender_male": brief.audience.gender_male,
            "gender_female": brief.audience.gender_female,
        },
    }


def task_affecting_snapshot(brief: StudyBrief) -> dict[str, Any]:
    """Stimulus fields that require rebuilding the generated task matrix."""
    snapshot = brief_change_snapshot(brief)
    return {
        "study_type": snapshot["study_type"],
        "categories": snapshot["categories"],
    }


def fingerprint_brief(brief: StudyBrief) -> str:
    payload = task_affecting_snapshot(brief)
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def diff_task_affecting(before: StudyBrief, after: StudyBrief) -> list[str]:
    a = task_affecting_snapshot(before)
    b = task_affecting_snapshot(after)
    return [key for key in ("study_type", "categories") if a.get(key) != b.get(key)]


def diff_brief_fields(before: StudyBrief, after: StudyBrief) -> list[str]:
    """Return all edited brief fields, including metadata that does not rebuild tasks."""
    a = brief_change_snapshot(before)
    b = brief_change_snapshot(after)
    changed: list[str] = []
    keys = [
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
    ]
    for key in keys:
        if a.get(key) != b.get(key):
            changed.append(key)
    return changed


RESEARCH_TIPS = [
    "Good research starts with clear categories — each set of images becomes a distinct research dimension.",
    "MindGenomic matrices balance exposure so every element gets a fair chance to be evaluated.",
    "Screening questions help you understand who your respondents are before they rate stimuli.",
    "A balanced age and gender split makes insights more representative of your target market.",
    "Preview your study as a respondent before launching — small wording tweaks can improve data quality.",
    "Once launched, respondents complete tasks independently; you can track progress from your dashboard.",
]


def research_tip_for_progress(progress: float) -> str:
    idx = min(len(RESEARCH_TIPS) - 1, max(0, int(progress // (100 / len(RESEARCH_TIPS)))))
    return RESEARCH_TIPS[idx]
