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


def test_default_classification_filled_when_missing() -> None:
    brief = StudyBrief(title="Demo")
    result = ensure_default_classification(brief)
    assert len(result.classification_questions) >= 1
    assert len(result.classification_questions[0].options) >= 2
