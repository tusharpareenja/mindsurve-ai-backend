"""Folder → category brief mapping."""

from __future__ import annotations

from app.schemas.study_brief import AttachmentBrief, StudyBrief
from app.services.folder_brief import (
    apply_folder_categories,
    element_name_from_filename,
    ensure_default_classification,
)


def test_element_name_strips_extension() -> None:
    assert element_name_from_filename("Aura.Shape1.png") == "Aura.Shape1"


def test_folder_categories_become_grid_structure() -> None:
    brief = StudyBrief(title="Demo")
    attachments = [
        AttachmentBrief(
            url="https://cdn.example/a1.png",
            filename="Aura.Shape1.png",
            content_type="image/png",
            category="Aura",
        ),
        AttachmentBrief(
            url="https://cdn.example/a2.png",
            filename="Aura.Shape2.png",
            content_type="image/png",
            category="Aura",
        ),
        AttachmentBrief(
            url="https://cdn.example/g1.png",
            filename="Garden.Shape1.png",
            content_type="image/png",
            category="Garden",
        ),
    ]
    result = apply_folder_categories(brief, attachments)
    assert result.study_type == "grid"
    assert [c.name for c in result.categories] == ["Aura", "Garden"]
    assert [e.name for e in result.categories[0].elements] == [
        "Aura.Shape1",
        "Aura.Shape2",
    ]
    assert result.categories[0].elements[0].content.endswith("a1.png")


def test_pdf_attachments_do_not_become_grid_elements() -> None:
    brief = StudyBrief(title="Copy test", study_type="text")
    attachments = [
        AttachmentBrief(
            url="https://cdn.example/brief.pdf",
            filename="campaign.docx.pdf",
            content_type="application/pdf",
        )
    ]
    result = apply_folder_categories(brief, attachments)
    assert result.study_type == "text"
    assert result.categories == []


def test_default_classification_filled_when_missing() -> None:
    brief = StudyBrief(title="Demo")
    result = ensure_default_classification(brief)
    assert len(result.classification_questions) >= 5
    assert len(result.classification_questions[0].options) >= 2


def test_default_classification_scales_with_respondents() -> None:
    from app.schemas.study_brief import AudienceBrief

    brief = StudyBrief(
        title="Large sample",
        audience=AudienceBrief(number_of_respondents=200),
    )
    result = ensure_default_classification(brief)
    assert len(result.classification_questions) >= 8
    assert all(len(q.options) >= 2 for q in result.classification_questions)


def test_smb_ai_study_replaces_packaging_screeners() -> None:
    from app.schemas.study_brief import (
        AudienceBrief,
        ClassificationQuestionBrief,
    )

    brief = StudyBrief(
        title="Positioning for AI Social Media Tool",
        background="AI tool for small business owners creating social media content.",
        study_type="text",
        audience=AudienceBrief(number_of_respondents=10),
        classification_questions=[
            ClassificationQuestionBrief(
                question_text="Do you usually notice packaging or visual design when shopping?",
                options=["Yes", "No"],
            ),
            ClassificationQuestionBrief(
                question_text="How familiar are you with this product category?",
                options=["Familiar", "Not familiar"],
            ),
        ],
    )
    result = ensure_default_classification(brief)
    texts = " ".join(q.question_text.lower() for q in result.classification_questions)
    assert "packaging" not in texts
    assert "product category" not in texts
    assert "small business" in texts or "social media" in texts or "ai tool" in texts
    assert len(result.classification_questions) >= 5
