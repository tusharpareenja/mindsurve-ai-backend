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


def test_root_image_plus_subfolders_become_layer_study() -> None:
    brief = StudyBrief(title="Pack")
    attachments = [
        AttachmentBrief(
            url="https://cdn.example/bg.png",
            filename="background.png",
            content_type="image/png",
            is_background=True,
        ),
        AttachmentBrief(
            url="https://cdn.example/logo1.png",
            filename="Logo/logo1.png",
            content_type="image/png",
            category="Logo",
            layer_order=0,
        ),
        AttachmentBrief(
            url="https://cdn.example/logo2.png",
            filename="Logo/logo2.png",
            content_type="image/png",
            category="Logo",
            layer_order=0,
        ),
        AttachmentBrief(
            url="https://cdn.example/logo3.png",
            filename="Logo/logo3.png",
            content_type="image/png",
            category="Logo",
            layer_order=0,
        ),
        AttachmentBrief(
            url="https://cdn.example/shape1.png",
            filename="Shape/shape1.png",
            content_type="image/png",
            category="Shape",
            layer_order=1,
        ),
        AttachmentBrief(
            url="https://cdn.example/shape2.png",
            filename="Shape/shape2.png",
            content_type="image/png",
            category="Shape",
            layer_order=1,
        ),
        AttachmentBrief(
            url="https://cdn.example/shape3.png",
            filename="Shape/shape3.png",
            content_type="image/png",
            category="Shape",
            layer_order=1,
        ),
        AttachmentBrief(
            url="https://cdn.example/color1.png",
            filename="Color/color1.png",
            content_type="image/png",
            category="Color",
            layer_order=2,
        ),
        AttachmentBrief(
            url="https://cdn.example/color2.png",
            filename="Color/color2.png",
            content_type="image/png",
            category="Color",
            layer_order=2,
        ),
        AttachmentBrief(
            url="https://cdn.example/color3.png",
            filename="Color/color3.png",
            content_type="image/png",
            category="Color",
            layer_order=2,
        ),
    ]
    result = apply_folder_categories(brief, attachments)
    assert result.study_type == "layer"
    assert result.background_image_url and result.background_image_url.endswith("bg.png")
    assert [layer.name for layer in result.layers] == ["Logo", "Shape", "Color"]
    assert [layer.z_index for layer in result.layers] == [0, 1, 2]
    assert len(result.layers[0].elements) == 3
    assert result.layers[0].elements[0].transform.width == 100
    assert result.categories == []


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
