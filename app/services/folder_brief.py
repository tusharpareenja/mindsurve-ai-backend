"""Apply user-provided folder → category image structure onto a study brief."""

from __future__ import annotations

import re
from collections import OrderedDict

from app.schemas.study_brief import (
    AttachmentBrief,
    CategoryBrief,
    ClassificationQuestionBrief,
    ElementBrief,
    StudyBrief,
)

_EXT = re.compile(r"\.[^.]+$")


def element_name_from_filename(filename: str) -> str:
    base = _EXT.sub("", filename.strip())
    return (base or filename).strip()[:150]


def apply_folder_categories(
    brief: StudyBrief, attachments: list[AttachmentBrief]
) -> StudyBrief:
    """If uploads include category folders, those become the grid categories."""
    data = brief.model_copy(deep=True)
    categorized = [a for a in attachments if a.category and a.url]
    if not categorized:
        # Still map flat uploaded images onto existing elements by filename when possible.
        by_name = {
            element_name_from_filename(a.filename).lower(): a
            for a in attachments
            if a.url and a.filename
        }
        if by_name and data.study_type == "grid":
            for cat in data.categories:
                for el in cat.elements:
                    key = element_name_from_filename(el.name).lower()
                    match = by_name.get(key) or by_name.get(el.name.lower())
                    if match and not el.content.strip():
                        el.element_type = "image"
                        el.content = match.url
                        el.name = element_name_from_filename(match.filename)
        return data

    buckets: OrderedDict[str, list[AttachmentBrief]] = OrderedDict()
    for item in categorized:
        cat = (item.category or "Category").strip()[:100] or "Category"
        buckets.setdefault(cat, []).append(item)

    data.study_type = "grid"
    data.categories = []
    for cat_name, items in buckets.items():
        elements: list[ElementBrief] = []
        for att in items:
            elements.append(
                ElementBrief(
                    name=element_name_from_filename(att.filename or "image"),
                    element_type="image",
                    content=att.url,
                    description="",
                )
            )
        data.categories.append(CategoryBrief(name=cat_name, elements=elements))
    return data


def ensure_default_classification(brief: StudyBrief) -> StudyBrief:
    """Guarantee at least one useful pre-study classification question."""
    data = brief.model_copy(deep=True)
    if data.classification_questions:
        return data
    data.classification_questions = [
        ClassificationQuestionBrief(
            question_text="Which option best describes your relationship to this category?",
            is_required=True,
            options=["Current customer", "Considering it", "Not interested", "Prefer not to say"],
        ),
        ClassificationQuestionBrief(
            question_text="How often do you shop for products like this?",
            is_required=True,
            options=["Weekly", "Monthly", "A few times a year", "Rarely / never"],
        ),
    ]
    return data
