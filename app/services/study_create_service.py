"""Create draft MindGenomic studies in the shared Unilever Postgres tables.

Does not modify Unilever application code — inserts into shared tables only.
Task generation is intentionally out of scope here.
"""

from __future__ import annotations

import logging
import secrets
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.schemas.study_brief import StudyBrief
from app.services.study_brief_validator import apply_defaults, is_brief_ready_for_create
from app.services.study_payload import audience_segmentation, category_uuid, element_uuid

logger = logging.getLogger(__name__)


def create_draft_study_from_brief(
    db: Session,
    *,
    creator_id: UUID,
    project_id: UUID,
    brief: StudyBrief,
) -> UUID:
    brief = apply_defaults(brief)
    if not is_brief_ready_for_create(brief):
        raise AppError(
            "Your study brief isn’t complete yet. Please finish the missing details first.",
            status_code=422,
        )
    if brief.study_type not in {"grid", "text"}:
        raise AppError("Only grid and text studies are supported right now.", status_code=422)

    settings = get_settings()
    study_id = uuid4()
    share_token = secrets.token_hex(16)
    base = (settings.STUDY_SHARE_BASE_URL or "https://mindsurve.com").rstrip("/")
    share_url = f"{base}/participate/{study_id}"

    rating = brief.rating_scale.model_dump()
    audience = audience_segmentation(brief)

    try:
        db.execute(
            text(
                """
                INSERT INTO studies (
                    id, title, background, language, main_question, orientation_text,
                    study_type, rating_scale, iped_parameters, creator_id, project_id,
                    status, share_token, share_url, toggle_shuffle, last_step,
                    total_responses, completed_responses, abandoned_responses
                ) VALUES (
                    :id, :title, :background, :language, :main_question, :orientation_text,
                    CAST(:study_type AS study_type_enum),
                    CAST(:rating_scale AS jsonb), CAST(:audience AS jsonb),
                    :creator_id, :project_id,
                    CAST('draft' AS study_status_enum), :share_token, :share_url,
                    false, 6, 0, 0, 0
                )
                """
            ),
            {
                "id": study_id,
                "title": brief.title.strip()[:255],
                "background": brief.background.strip(),
                "language": (brief.language or "en")[:10],
                "main_question": brief.main_question.strip(),
                "orientation_text": brief.orientation_text.strip(),
                "study_type": brief.study_type,
                "rating_scale": _json(rating),
                "audience": _json(audience),
                "creator_id": creator_id,
                "project_id": project_id,
                "share_token": share_token,
                "share_url": share_url,
            },
        )

        for order, cat in enumerate(brief.categories):
            cat_row_id = uuid4()
            cat_logical_id = category_uuid(study_id, cat.name)
            db.execute(
                text(
                    """
                    INSERT INTO study_categories (
                        id, study_id, category_id, name, "order", phase_type
                    ) VALUES (
                        :id, :study_id, :category_id, :name, :order,
                        CAST(:phase_type AS study_type_enum)
                    )
                    """
                ),
                {
                    "id": cat_row_id,
                    "study_id": study_id,
                    "category_id": cat_logical_id,
                    "name": cat.name.strip()[:100],
                    "order": order,
                    "phase_type": brief.study_type,
                },
            )
            for el in cat.elements:
                el_id = element_uuid(study_id, cat.name, el.name)
                content = (el.content or el.name).strip()
                if brief.study_type == "text":
                    element_type = "text"
                    content = content[:150]
                else:
                    element_type = el.element_type if el.element_type in {"image", "text"} else "image"
                db.execute(
                    text(
                        """
                        INSERT INTO study_elements (
                            id, study_id, category_id, element_id, name, description,
                            element_type, content, alt_text
                        ) VALUES (
                            :id, :study_id, :category_id, :element_id, :name, :description,
                            CAST(:element_type AS element_type_enum), :content, :alt_text
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "study_id": study_id,
                        "category_id": cat_row_id,
                        "element_id": el_id,
                        "name": el.name.strip()[:1000],
                        "description": (el.description or None),
                        "element_type": element_type,
                        "content": content,
                        "alt_text": el.name.strip()[:200] or None,
                    },
                )

        for q_order, q in enumerate(brief.classification_questions, start=1):
            options = []
            for opt_i, opt_text in enumerate(q.options):
                options.append(
                    {
                        "id": chr(ord("A") + opt_i) if opt_i < 26 else str(opt_i),
                        "text": opt_text[:200],
                        "order": opt_i + 1,
                    }
                )
            db.execute(
                text(
                    """
                    INSERT INTO study_classification_questions (
                        id, study_id, question_id, question_text, question_type,
                        is_required, "order", answer_options, config
                    ) VALUES (
                        :id, :study_id, :question_id, :question_text, :question_type,
                        :is_required, :order, CAST(:answer_options AS jsonb),
                        CAST(:config AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "study_id": study_id,
                    "question_id": f"Q{q_order}"[:10],
                    "question_text": q.question_text.strip()[:500],
                    "question_type": "multiple_choice",
                    "is_required": "Y" if q.is_required else "N",
                    "order": q_order,
                    "answer_options": _json(options),
                    "config": _json({"optional_classification_question": False}),
                },
            )
    except AppError:
        raise
    except Exception:
        logger.exception("Failed to create draft study from brief")
        raise AppError(
            "We couldn’t create your study. Please try again.",
            status_code=500,
        ) from None

    return study_id


def _json(value: object) -> str:
    import json

    return json.dumps(value)
