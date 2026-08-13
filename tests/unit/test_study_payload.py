"""Unit tests for study generation payload + fingerprints."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.schemas.study_brief import (
    AudienceBrief,
    CategoryBrief,
    ClassificationQuestionBrief,
    ElementBrief,
    RatingScaleBrief,
    StudyBrief,
)
from app.services.study_payload import (
    build_generate_tasks_payload,
    category_uuid,
    diff_task_affecting,
    element_uuid,
    fingerprint_brief,
)


def _sample_brief() -> StudyBrief:
    return StudyBrief(
        title="Shape Perception Study",
        background="Understand reactions to shape sets.",
        language="en",
        study_type="grid",
        main_question="How appealing is this shape?",
        orientation_text="Rate each image based on your first impression.",
        rating_scale=RatingScaleBrief(min_label="Not at all", max_label="Extremely"),
        categories=[
            CategoryBrief(
                name="Aura",
                elements=[
                    ElementBrief(
                        name="Aura.Shape1",
                        element_type="image",
                        content="https://cdn.example/a1.png",
                    ),
                    ElementBrief(
                        name="Aura.Shape2",
                        element_type="image",
                        content="https://cdn.example/a2.png",
                    ),
                ],
            ),
            CategoryBrief(
                name="Garden",
                elements=[
                    ElementBrief(
                        name="Garden.Shape1",
                        element_type="image",
                        content="https://cdn.example/g1.png",
                    ),
                    ElementBrief(
                        name="Garden.Shape2",
                        element_type="image",
                        content="https://cdn.example/g2.png",
                    ),
                ],
            ),
        ],
        classification_questions=[
            ClassificationQuestionBrief(
                question_text="How often do you notice brand shapes?",
                options=["Rarely", "Sometimes", "Often"],
            ),
            ClassificationQuestionBrief(
                question_text="Do you follow design trends?",
                options=["Yes", "No"],
            ),
            ClassificationQuestionBrief(
                question_text="Have you bought a product for its packaging?",
                options=["Yes", "No"],
            ),
        ],
        audience=AudienceBrief(
            number_of_respondents=10,
            age_segments=["18-24"],
            age_distribution={"18-24": 100},
            countries=["India"],
            gender_male=60,
            gender_female=40,
        ),
    )


def test_deterministic_ids_are_stable() -> None:
    study_id = UUID("11111111-1111-1111-1111-111111111111")
    assert category_uuid(study_id, "Aura") == category_uuid(study_id, "Aura")
    assert element_uuid(study_id, "Aura", "Aura.Shape1") == element_uuid(
        study_id, "Aura", "Aura.Shape1"
    )
    assert category_uuid(study_id, "Aura") != category_uuid(study_id, "Garden")


def test_build_generate_tasks_payload_shape() -> None:
    brief = _sample_brief()
    study_id = uuid4()
    payload = build_generate_tasks_payload(brief, study_id)

    assert payload["study_id"] == str(study_id)
    assert payload["study_type"] == "grid"
    assert payload["audience_segmentation"]["number_of_respondents"] == 10
    assert payload["audience_segmentation"]["gender_distribution"] == {
        "male": 60,
        "female": 40,
    }
    assert len(payload["categories"]) == 2
    assert len(payload["elements"]) == 4
    assert payload["categories"][0]["category_id"] == str(category_uuid(study_id, "Aura"))
    assert payload["elements"][0]["category_id"] == payload["categories"][0]["category_id"]
    assert len(payload["classification_questions"]) == 3


def test_fingerprint_changes_when_element_changes() -> None:
    brief = _sample_brief()
    original = fingerprint_brief(brief)
    brief.categories[0].elements[0].name = "Aura.Shape1-renamed"
    assert fingerprint_brief(brief) != original


def test_diff_task_affecting_detects_audience_and_categories() -> None:
    before = _sample_brief()
    after = before.model_copy(deep=True)
    after.audience.gender_male = 50
    after.audience.gender_female = 50
    after.categories[0].elements[0].content = "https://cdn.example/changed.png"
    changed = diff_task_affecting(before, after)
    assert "audience" in changed
    assert "categories" in changed
