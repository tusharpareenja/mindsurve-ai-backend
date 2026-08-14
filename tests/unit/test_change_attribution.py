"""Reported changes must match what the user actually asked for."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.schemas.study_brief import (
    AudienceBrief,
    CategoryBrief,
    ClassificationQuestionBrief,
    ElementBrief,
    StudyBrief,
)
from app.services.audience_infer import apply_inferred_audience
from app.services.folder_brief import ensure_default_classification
from app.services.study_brief_service import StudyBriefService
from app.services.synthetic_capacity import min_classification_question_count


def _brief() -> StudyBrief:
    return StudyBrief(
        title="Deodorant Visual Appeal Study",
        background="Testing pack visuals.",
        study_type="text",
        main_question="How much do you agree?",
        orientation_text="Rate each message.",
        categories=[
            CategoryBrief(
                name="Freshness",
                elements=[
                    ElementBrief(
                        name="Stays fresh all day.",
                        element_type="text",
                        content="Stays fresh all day.",
                    )
                ],
            )
        ],
        audience=AudienceBrief(number_of_respondents=5, countries=["India"]),
    )


def test_audience_inferred_before_the_ai_call_is_still_reported() -> None:
    """The pre-AI inference used to hide audience edits from the changelog."""
    stored = _brief()
    corpus = "target it for usa and age group bw 18-65 and 10 respondents"

    # Mirrors run_ai_turn: audience is inferred onto the brief before the AI call.
    pre_ai = apply_inferred_audience(stored.model_copy(deep=True), text=corpus)
    final = pre_ai.model_copy(deep=True)

    service = StudyBriefService(MagicMock())
    text = service._with_change_details(
        "I've updated the audience.", final, ["audience"], before=stored
    )

    assert final.audience.number_of_respondents == 10
    assert final.audience.countries == ["United States"]
    assert "- Audience: 10 respondents · United States" in text


def test_changelog_lists_only_newly_added_screening_questions() -> None:
    before = _brief()
    before.classification_questions = [
        ClassificationQuestionBrief(
            question_text="How often do you buy deodorant?",
            options=["Weekly", "Monthly"],
        )
    ]
    after = before.model_copy(deep=True)
    after.classification_questions.append(
        ClassificationQuestionBrief(
            question_text="How do you feel about trying new brands?",
            options=["Love it", "Avoid it"],
        )
    )

    service = StudyBriefService(MagicMock())
    text = service._with_change_details(
        "Updated screening.", after, ["classification_questions"], before=before
    )

    assert "- Screening questions: 2 (1 new)" in text
    assert "Added: How do you feel about trying new brands?" in text
    # The question that was already there is not re-listed as a change.
    assert "How often do you buy deodorant?" not in text


def test_deleted_screening_question_is_not_restored_by_the_capacity_backfill() -> None:
    """Removing a question used to be undone by the min-question padding."""
    brief = _brief()
    brief.audience = AudienceBrief(number_of_respondents=100, countries=["India"])
    floor = min_classification_question_count(100)
    unwanted = "How often do you come across this kind of product or idea?"

    at_floor = ensure_default_classification(brief)
    assert len(at_floor.classification_questions) == floor
    assert any(
        q.question_text == unwanted for q in at_floor.classification_questions
    )

    after_delete = at_floor.model_copy(deep=True)
    after_delete.classification_questions = [
        q for q in at_floor.classification_questions if q.question_text != unwanted
    ]

    result = ensure_default_classification(after_delete, avoid_texts=[unwanted])

    # Capacity is still respected, but the deleted question stays deleted.
    assert len(result.classification_questions) == floor
    assert all(q.question_text != unwanted for q in result.classification_questions)
