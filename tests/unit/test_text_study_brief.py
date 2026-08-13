"""Text-study validation limits (3–20 categories, 3–20 statements, 150 chars)."""

from __future__ import annotations

from app.schemas.study_brief import (
    AudienceBrief,
    CategoryBrief,
    ClassificationQuestionBrief,
    ElementBrief,
    RatingScaleBrief,
    StudyBrief,
)
from app.services.study_brief_validator import apply_defaults, compute_missing_fields


def _text_brief(*, cats: int, statements: int, extra: str = "") -> StudyBrief:
    categories = [
        CategoryBrief(
            name=f"Theme {i + 1}",
            elements=[
                ElementBrief(
                    name=f"Statement {i + 1}.{j + 1}",
                    element_type="text",
                    content=f"Statement {i + 1}.{j + 1}{extra}",
                )
                for j in range(statements)
            ],
        )
        for i in range(cats)
    ]
    return StudyBrief(
        title="Text study",
        background="People rate short statements about the idea.",
        study_type="text",
        main_question="How much do you agree with this statement?",
        orientation_text="Rate each statement.",
        rating_scale=RatingScaleBrief(),
        categories=categories,
        classification_questions=[
            ClassificationQuestionBrief(
                question_text="Have you seen ads in this category recently?",
                options=["Yes", "No"],
            )
        ],
        audience=AudienceBrief(
            number_of_respondents=10,
            age_segments=["18-24"],
            age_distribution={"18-24": 100},
            countries=["India"],
            gender_male=50,
            gender_female=50,
        ),
    )


def test_text_study_ready_at_min_structure() -> None:
    brief = apply_defaults(_text_brief(cats=3, statements=3))
    assert compute_missing_fields(brief) == []


def test_text_study_rejects_fewer_than_three_categories() -> None:
    brief = apply_defaults(_text_brief(cats=2, statements=3))
    missing = compute_missing_fields(brief)
    assert "categories_min_3" in missing


def test_text_study_rejects_short_category() -> None:
    brief = apply_defaults(_text_brief(cats=3, statements=2))
    missing = compute_missing_fields(brief)
    assert any("elements_min_3" in item for item in missing)


def test_text_study_rejects_statement_over_150_chars() -> None:
    brief = _text_brief(cats=3, statements=3, extra="x" * 160)
    # Skip apply_defaults so we observe the too-long error before truncation.
    missing = compute_missing_fields(brief)
    assert any("too_long" in item for item in missing)


def test_apply_defaults_truncates_text_statements() -> None:
    brief = apply_defaults(_text_brief(cats=3, statements=3, extra="x" * 160))
    for cat in brief.categories:
        for el in cat.elements:
            assert len(el.content) <= 150
            assert el.element_type == "text"


def test_grid_study_still_allows_two_categories() -> None:
    brief = StudyBrief(
        title="Grid study",
        background="Visual test",
        study_type="grid",
        main_question="How do you feel?",
        orientation_text="Rate the visuals.",
        categories=[
            CategoryBrief(
                name="A",
                elements=[
                    ElementBrief(name="a1", element_type="image", content="https://x/a1.png"),
                    ElementBrief(name="a2", element_type="image", content="https://x/a2.png"),
                ],
            ),
            CategoryBrief(
                name="B",
                elements=[
                    ElementBrief(name="b1", element_type="image", content="https://x/b1.png"),
                    ElementBrief(name="b2", element_type="image", content="https://x/b2.png"),
                ],
            ),
        ],
        classification_questions=[
            ClassificationQuestionBrief(question_text="Q?", options=["Yes", "No"])
        ],
        audience=AudienceBrief(
            number_of_respondents=10,
            age_segments=["18-24"],
            age_distribution={"18-24": 100},
            countries=["India"],
        ),
    )
    assert "categories_min_2" not in compute_missing_fields(brief)
    assert compute_missing_fields(brief, require_grid_images=True) == []


def test_heuristic_no_images_builds_text_study() -> None:
    from types import SimpleNamespace

    from app.services.study_brief_service import StudyBriefService

    service = StudyBriefService(db=SimpleNamespace())  # type: ignore[arg-type]
    payload = service._heuristic_ai(
        StudyBrief(),
        "I don't have images — please make a text study about how people connect with nature",
        [],
    )
    assert payload["intent"] == "build"
    brief = StudyBrief.model_validate(payload["study_brief"])
    assert brief.study_type == "text"
    assert len(brief.categories) >= 3
    assert all(len(c.elements) >= 3 for c in brief.categories)
    assert all(el.element_type == "text" for c in brief.categories for el in c.elements)
    joined = " ".join(el.content for c in brief.categories for el in c.elements)
    assert "don't have images" not in joined.lower()
    assert "this idea" not in joined.lower()


def test_ensure_text_study_pads_single_category() -> None:
    from app.services.text_brief import ensure_text_study_structure

    brief = StudyBrief(
        title="Testing Instagram Reels Openings for Weight Loss",
        study_type="text",
        categories=[
            CategoryBrief(
                name="Opening Statements",
                elements=[
                    ElementBrief(
                        name="Lose weight without giving up your favorite foods!",
                        element_type="text",
                        content="Lose weight without giving up your favorite foods!",
                    ),
                    ElementBrief(
                        name="Transform your body in just 30 days!",
                        element_type="text",
                        content="Transform your body in just 30 days!",
                    ),
                    ElementBrief(
                        name="Join the weight loss challenge today!",
                        element_type="text",
                        content="Join the weight loss challenge today!",
                    ),
                    ElementBrief(
                        name="Discover the secret to sustainable weight loss!",
                        element_type="text",
                        content="Discover the secret to sustainable weight loss!",
                    ),
                    ElementBrief(
                        name="Start your journey to a healthier you!",
                        element_type="text",
                        content="Start your journey to a healthier you!",
                    ),
                ],
            )
        ],
    )
    result = ensure_text_study_structure(brief)
    title = "Testing Instagram Reels Openings for Weight Loss"
    assert len(result.categories) >= 3
    assert result.categories[0].name == "Opening Statements"
    originals = [
        "Lose weight without giving up your favorite foods!",
        "Transform your body in just 30 days!",
        "Join the weight loss challenge today!",
        "Discover the secret to sustainable weight loss!",
        "Start your journey to a healthier you!",
    ]
    kept = [(el.content or el.name) for el in result.categories[0].elements]
    assert kept == originals
    assert all(len(c.elements) >= 3 for c in result.categories)
    for cat in result.categories:
        for el in cat.elements:
            line = el.content or el.name
            assert title.lower() not in line.lower()
            assert "instagram reel" not in line.lower()
            assert "this hook" not in line.lower()
    extra_names = {c.name for c in result.categories[1:]}
    assert extra_names & {"Bold claim", "Relatable struggle", "Question hook"}


def test_ensure_text_study_strips_title_meta_lines() -> None:
    from app.services.text_brief import ensure_text_study_structure

    title = "Testing Instagram Reels Openings for Weight Loss"
    brief = StudyBrief(
        title=title,
        background="Find which Reel opening makes people trying to lose weight stop scrolling.",
        study_type="text",
        categories=[
            CategoryBrief(
                name="Opening Statements",
                elements=[
                    ElementBrief(
                        name="Lose weight without giving up your favorite foods!",
                        element_type="text",
                        content="Lose weight without giving up your favorite foods!",
                    ),
                    ElementBrief(
                        name=f"Tired of trying {title} and seeing nothing change?",
                        element_type="text",
                        content=f"Tired of trying {title} and seeing nothing change?",
                    ),
                    ElementBrief(
                        name="This hook makes people pause mid-scroll",
                        element_type="text",
                        content="This hook makes people pause mid-scroll",
                    ),
                ],
            )
        ],
    )
    result = ensure_text_study_structure(brief)
    first = [el.content for el in result.categories[0].elements]
    assert "Lose weight without giving up your favorite foods!" in first
    assert all(title.lower() not in (line or "").lower() for line in first)
    assert all("this hook" not in (line or "").lower() for line in first)
    for cat in result.categories:
        for el in cat.elements:
            assert title.lower() not in (el.content or "").lower()


def test_ensure_text_study_ignores_grid() -> None:
    from app.services.text_brief import ensure_text_study_structure

    brief = StudyBrief(study_type="grid", categories=[])
    assert ensure_text_study_structure(brief).categories == []
