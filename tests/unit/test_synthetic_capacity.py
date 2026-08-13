"""Unit tests for synthetic AI respondent capacity capping."""

from __future__ import annotations

from app.schemas.study_brief import (
    AudienceBrief,
    ClassificationQuestionBrief,
    RatingScaleBrief,
    StudyBrief,
)
from app.services.synthetic_capacity import (
    max_ai_respondents_from_brief,
    min_classification_question_count,
    resolve_synthetic_respondent_count,
)


def _brief(n_questions: int = 5, options_per: int = 2, respondents: int = 50) -> StudyBrief:
    questions = [
        ClassificationQuestionBrief(
            question_text=f"Screening question {i + 1}?",
            options=[f"Opt{j + 1}" for j in range(options_per)],
        )
        for i in range(n_questions)
    ]
    return StudyBrief(
        title="Capacity Test",
        background="Background",
        study_type="grid",
        main_question="How do you feel when seeing these visuals?",
        orientation_text="Rate each image.",
        rating_scale=RatingScaleBrief(min_label="Low", max_label="High"),
        classification_questions=questions,
        audience=AudienceBrief(
            number_of_respondents=respondents,
            age_segments=["18-24"],
            age_distribution={"18-24": 100},
            countries=["India"],
            gender_male=50,
            gender_female=50,
        ),
    )


def test_max_ai_is_two_to_the_n_for_binary_screeners() -> None:
    brief = _brief(n_questions=5, options_per=2)
    assert max_ai_respondents_from_brief(brief) == 32


def test_max_ai_follows_option_product_for_any_count() -> None:
    brief = _brief(n_questions=4, options_per=2)
    assert max_ai_respondents_from_brief(brief) == 16


def test_max_ai_zero_when_no_questions() -> None:
    brief = _brief(n_questions=0, options_per=2)
    assert max_ai_respondents_from_brief(brief) == 0


def test_cap_requested_above_capacity() -> None:
    brief = _brief(respondents=50)
    actual, capacity = resolve_synthetic_respondent_count(brief, requested=50)
    assert capacity == 32
    assert actual == 32


def test_use_requested_when_below_capacity() -> None:
    brief = _brief(respondents=20)
    actual, capacity = resolve_synthetic_respondent_count(brief, requested=20)
    assert capacity == 32
    assert actual == 20


def test_min_classification_count_from_respondents() -> None:
    assert min_classification_question_count(20) == 5
    assert min_classification_question_count(32) == 5
    assert min_classification_question_count(33) == 6
    assert min_classification_question_count(200) == 8
    assert min_classification_question_count(300) == 9
