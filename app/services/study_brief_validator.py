"""Validate study briefs against create-study hard rules."""

from __future__ import annotations

from app.schemas.study_brief import (
    MAX_GRID_CATEGORIES,
    MAX_GRID_ELEMENTS,
    MAX_LAYER_ELEMENTS,
    MAX_LAYER_LAYERS,
    MAX_STATEMENT_CHARS,
    MAX_TEXT_CATEGORIES,
    MAX_TEXT_STATEMENTS,
    MIN_GRID_CATEGORIES,
    MIN_GRID_ELEMENTS,
    MIN_LAYER_ELEMENTS,
    MIN_LAYER_LAYERS,
    MIN_TEXT_CATEGORIES,
    MIN_TEXT_STATEMENTS,
    StudyBrief,
)


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
    if brief.study_type not in {"grid", "text", "layer"}:
        missing.append("study_type")
    if not brief.main_question.strip():
        missing.append("main_question")
    if not brief.orientation_text.strip():
        missing.append("orientation_text")
    if not brief.rating_scale.min_label.strip() or not brief.rating_scale.max_label.strip():
        missing.append("rating_scale_labels")

    if brief.study_type == "layer":
        layers = brief.layers
        if not (brief.background_image_url or "").strip():
            missing.append("background_image")
        if len(layers) < MIN_LAYER_LAYERS:
            missing.append(f"layers_min_{MIN_LAYER_LAYERS}")
        if len(layers) > MAX_LAYER_LAYERS:
            missing.append(f"layers_max_{MAX_LAYER_LAYERS}")
        for idx, layer in enumerate(layers):
            if not layer.name.strip():
                missing.append(f"layer_{idx + 1}_name")
            if len(layer.elements) < MIN_LAYER_ELEMENTS:
                missing.append(f"layer_{idx + 1}_elements_min_{MIN_LAYER_ELEMENTS}")
            if len(layer.elements) > MAX_LAYER_ELEMENTS:
                missing.append(f"layer_{idx + 1}_elements_max_{MAX_LAYER_ELEMENTS}")
            for eidx, el in enumerate(layer.elements):
                if not el.name.strip():
                    missing.append(f"layer_{idx + 1}_element_{eidx + 1}_name")
                if require_grid_images and not (el.content or "").strip():
                    missing.append(f"layer_{idx + 1}_element_{eidx + 1}_image")
    else:
        cats = brief.categories
        is_text = brief.study_type == "text"
        min_cats = MIN_TEXT_CATEGORIES if is_text else MIN_GRID_CATEGORIES
        max_cats = MAX_TEXT_CATEGORIES if is_text else MAX_GRID_CATEGORIES
        min_els = MIN_TEXT_STATEMENTS if is_text else MIN_GRID_ELEMENTS
        max_els = MAX_TEXT_STATEMENTS if is_text else MAX_GRID_ELEMENTS

        if len(cats) < min_cats:
            missing.append(f"categories_min_{min_cats}")
        if len(cats) > max_cats:
            missing.append(f"categories_max_{max_cats}")

        for idx, cat in enumerate(cats):
            if not cat.name.strip():
                missing.append(f"category_{idx + 1}_name")
            if len(cat.elements) < min_els:
                missing.append(f"category_{idx + 1}_elements_min_{min_els}")
            if len(cat.elements) > max_els:
                missing.append(f"category_{idx + 1}_elements_max_{max_els}")
            for eidx, el in enumerate(cat.elements):
                if not el.name.strip():
                    missing.append(f"category_{idx + 1}_element_{eidx + 1}_name")
                if is_text:
                    statement = (el.content or el.name).strip()
                    if not statement:
                        missing.append(f"category_{idx + 1}_element_{eidx + 1}_content")
                    elif len(statement) > MAX_STATEMENT_CHARS:
                        missing.append(f"category_{idx + 1}_element_{eidx + 1}_too_long")
                if (
                    require_grid_images
                    and brief.study_type == "grid"
                    and el.element_type == "image"
                    and not el.content.strip()
                ):
                    missing.append(f"category_{idx + 1}_element_{eidx + 1}_image")

    # AI is prompted to generate ≥5; users may add/remove freely (keep ≥1).
    # Unilever only requires ≥1 classification question for synthetic panelists.
    if len(brief.classification_questions) < 1:
        missing.append("classification_questions_min_1")
    for qidx, q in enumerate(brief.classification_questions):
        if not q.question_text.strip():
            missing.append(f"classification_{qidx + 1}_text")
        if len([opt for opt in q.options if opt and str(opt).strip()]) < 2:
            missing.append(f"classification_{qidx + 1}_options")

    # Audience mirrors the Unilever audience step.
    aud = brief.audience
    if not aud.number_of_respondents or aud.number_of_respondents < 1:
        missing.append("respondents")
    if not aud.age_distribution:
        missing.append("age_distribution")
    elif sum(aud.age_distribution.values()) != 100:
        missing.append("age_distribution_total_100")
    if not aud.countries:
        missing.append("countries")
    if aud.gender_male + aud.gender_female != 100:
        missing.append("gender_distribution_total_100")

    seen: set[str] = set()
    ordered: list[str] = []
    for item in missing:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def is_brief_ready_for_review(brief: StudyBrief) -> bool:
    """Structure + copy complete (grid/layer images may still be uploading)."""
    return len(compute_missing_fields(brief, require_grid_images=False)) == 0


def is_brief_ready_for_create(brief: StudyBrief) -> bool:
    """Ready to insert into shared studies tables."""
    return len(compute_missing_fields(brief, require_grid_images=True)) == 0


def _default_main_question(study_type: str | None) -> str:
    """In-task rating prompt respondents see with each stimulus."""
    if study_type == "text":
        return "How much do you agree with this statement?"
    if study_type == "layer":
        return "How do you feel when seeing this design?"
    return "How do you feel when seeing these visuals?"


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

    if not data.main_question.strip() and data.study_type in {"grid", "text", "layer"}:
        data.main_question = _default_main_question(data.study_type)
    if not data.orientation_text.strip() and data.study_type in {"grid", "text", "layer"}:
        if data.study_type == "text":
            data.orientation_text = (
                "You will see several short statements. Rate each one based on "
                "your first impression."
            )
        elif data.study_type == "layer":
            data.orientation_text = (
                "You will see several layered designs. Rate each one based on "
                "your first impression."
            )
        else:
            data.orientation_text = (
                "You will see several visuals. Rate each one based on your "
                "first impression."
            )

    if data.study_type == "text":
        for cat in data.categories:
            for el in cat.elements:
                el.element_type = "text"
                statement = (el.content or el.name).strip()[:MAX_STATEMENT_CHARS]
                el.content = statement
                if not el.name.strip():
                    el.name = statement
                else:
                    el.name = el.name.strip()[:MAX_STATEMENT_CHARS]
    elif data.study_type == "grid":
        for cat in data.categories:
            for el in cat.elements:
                if el.element_type not in {"image", "text"}:
                    el.element_type = "image"
    elif data.study_type == "layer":
        for order, layer in enumerate(data.layers):
            layer.z_index = order
            layer.order = order
            if layer.transform is None:
                from app.schemas.study_brief import LayerTransformBrief, DEFAULT_LAYER_TRANSFORM

                layer.transform = LayerTransformBrief(**DEFAULT_LAYER_TRANSFORM)
            for eidx, el in enumerate(layer.elements):
                el.order = eidx

    data.missing_fields = compute_missing_fields(data, require_grid_images=True)
    if data.status not in {"confirmed", "created"}:
        if is_brief_ready_for_review(data):
            data.status = "ready"
        else:
            data.status = "gathering"
    return data
