"""Helpers for AI synthetic respondent capacity from screening questions."""

from __future__ import annotations

import math

from app.schemas.study_brief import StudyBrief

# Floor question count (options per question may be 2+; capacity = product of counts).
MIN_CLASSIFICATION_QUESTIONS = 5


def min_classification_question_count(respondents: int | None) -> int:
    """
    Minimum screening questions so N can be covered even if every question has
    only 2 options (conservative floor). Real capacity = product of option counts.

    Uses max(5, ceil(log2(N))) — e.g. 200 → 8, 300 → 9.
    """
    n = int(respondents or 0)
    if n <= 1:
        return MIN_CLASSIFICATION_QUESTIONS
    needed = math.ceil(math.log2(n))
    return max(MIN_CLASSIFICATION_QUESTIONS, needed)


def max_ai_respondents_from_brief(brief: StudyBrief) -> int:
    """
    Max synthetic respondents = product of option counts across classification questions
    (same rule as Unilever ``get_max_panelist_combinations`` / panelist_generator).

    Example: 5 questions × 2 options each → 2^5 = 32.
    Returns 0 if there are no valid screeners (need ≥1 question with ≥2 options).
    """
    questions = brief.classification_questions or []
    if not questions:
        return 0

    total = 1
    for question in questions:
        options = [opt.strip() for opt in question.options if opt and opt.strip()]
        if len(options) < 2:
            return 0
        total *= len(options)
    return total


def resolve_synthetic_respondent_count(
    brief: StudyBrief,
    *,
    requested: int | None = None,
) -> tuple[int, int]:
    """
    Return (actual_to_run, max_ai_capacity).

    actual = min(requested, max_ai) when requested > 0, else max_ai.
    """
    max_ai = max_ai_respondents_from_brief(brief)
    want = int(requested if requested is not None else (brief.audience.number_of_respondents or 0))
    if max_ai <= 0:
        return 0, 0
    if want <= 0:
        return max_ai, max_ai
    return min(want, max_ai), max_ai
