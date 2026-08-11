"""Validate study briefs against create-study hard rules."""

from __future__ import annotations

from app.schemas.study_brief import StudyBrief


def compute_missing_fields(
    brief: StudyBrief,
    *,
    require_grid_images: bool = False,
) -> list[str]:
    missing: list[str] = []
    title = brief.title.strip()
    if len(title) < 3:
        missing.append("title")
    if not brief.background.strip():
        missing.append("background")
    if brief.study_type not in {"grid", "text"}:
        missing.append("study_type")
    if not brief.main_question.strip():
        missing.append("main_question")
    if not brief.orientation_text.strip():
        missing.append("orientation_text")
    if not brief.rating_scale.min_label.strip() or not brief.rating_scale.max_label.strip():
        missing.append("rating_scale_labels")

    cats = brief.categories
    if len(cats) < 3:
        missing.append("categories_min_3")
    if len(cats) > 15:
        missing.append("categories_max_15")

    for idx, cat in enumerate(cats):
        if not cat.name.strip():
            missing.append(f"category_{idx + 1}_name")
        if len(cat.elements) < 3:
            missing.append(f"category_{idx + 1}_elements_min_3")
        if len(cat.elements) > 10:
            missing.append(f"category_{idx + 1}_elements_max_10")
        for eidx, el in enumerate(cat.elements):
            if not el.name.strip():
                missing.append(f"category_{idx + 1}_element_{eidx + 1}_name")
            if brief.study_type == "text" and not (el.content or el.name).strip():
                missing.append(f"category_{idx + 1}_element_{eidx + 1}_content")
            if (
                require_grid_images
                and brief.study_type == "grid"
                and el.element_type == "image"
                and not el.content.strip()
            ):
                missing.append(f"category_{idx + 1}_element_{eidx + 1}_image")

    for qidx, q in enumerate(brief.classification_questions):
        if not q.question_text.strip():
            missing.append(f"classification_{qidx + 1}_text")
        if len(q.options) < 2:
            missing.append(f"classification_{qidx + 1}_options")

    seen: set[str] = set()
    ordered: list[str] = []
    for item in missing:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def is_brief_ready_for_review(brief: StudyBrief) -> bool:
    """Structure + copy complete (grid images may still be uploading)."""
    return len(compute_missing_fields(brief, require_grid_images=False)) == 0


def is_brief_ready_for_create(brief: StudyBrief) -> bool:
    """Ready to insert into shared studies tables."""
    return len(compute_missing_fields(brief, require_grid_images=True)) == 0


def apply_defaults(brief: StudyBrief) -> StudyBrief:
    data = brief.model_copy(deep=True)
    if not data.language:
        data.language = "en"
    if not data.rating_scale.min_label:
        data.rating_scale.min_label = "Not at all"
    if not data.rating_scale.max_label:
        data.rating_scale.max_label = "Extremely"
    data.rating_scale.min_value = 1
    data.rating_scale.max_value = 5

    if data.study_type == "text":
        for cat in data.categories:
            for el in cat.elements:
                el.element_type = "text"
                if not el.content.strip():
                    el.content = el.name
    elif data.study_type == "grid":
        for cat in data.categories:
            for el in cat.elements:
                if el.element_type not in {"image", "text"}:
                    el.element_type = "image"

    data.missing_fields = compute_missing_fields(data, require_grid_images=True)
    if data.status not in {"confirmed", "created"}:
        if is_brief_ready_for_review(data):
            data.status = "ready"
        else:
            data.status = "gathering"
    return data
