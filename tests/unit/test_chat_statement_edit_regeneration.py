"""Chat edits to generated stimuli must be proposed, not silently applied or refused."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.schemas.study_brief import CategoryBrief, ElementBrief, StudyBrief
from app.services.study_brief_service import StudyBriefService


def _text_brief() -> StudyBrief:
    return StudyBrief(
        title="Automotive Advertising Messaging Study",
        background="Testing dealership messaging.",
        study_type="text",
        main_question="How much do you agree?",
        orientation_text="Rate each message.",
        categories=[
            CategoryBrief(
                name="Broad Market Messaging",
                elements=[
                    ElementBrief(
                        name="Best message: real price + financing + protection + simple CTA.",
                        element_type="text",
                        content="Best message: real price + financing + protection + simple CTA.",
                    )
                ],
            )
        ],
        status="created",
        study_id=uuid4(),
    )


def _service_with_run(status: str | None) -> StudyBriefService:
    service = StudyBriefService(MagicMock())
    run = MagicMock()
    run.status = status
    service.generation_repo.latest_for_chat = MagicMock(
        return_value=run if status else None
    )
    return service


def test_statement_edit_after_generation_needs_confirmation() -> None:
    service = _service_with_run("ready")
    assert service._changes_require_regeneration(uuid4(), ["categories"]) is True


def test_metadata_edit_after_generation_applies_directly() -> None:
    service = _service_with_run("ready")
    changed = ["main_question", "orientation_text", "classification_questions"]
    assert service._changes_require_regeneration(uuid4(), changed) is False


def test_statement_edit_before_generation_applies_directly() -> None:
    service = _service_with_run(None)
    assert service._changes_require_regeneration(uuid4(), ["categories"]) is False


def test_proposal_shows_prepared_statement_before_and_after() -> None:
    current = _text_brief()
    proposed = current.model_copy(deep=True)
    proposed.categories[0].elements[0].content = (
        "Real price, financing, and protection — see it now."
    )

    proposal = StudyBriefService._regeneration_proposal(
        current, proposed, ["categories"]
    )

    assert proposal["changed_fields"] == ["categories"]
    assert proposal["patch"]["categories"][0]["elements"][0]["content"].startswith(
        "Real price"
    )

    preview = proposal["preview"]
    assert preview["summary"] == "1 rewritten"
    assert preview["total"] == 1
    item = preview["items"][0]
    assert item["type"] == "edited"
    assert item["category"] == "Broad Market Messaging"
    assert item["before"].startswith("Best message:")
    assert item["after"].startswith("Real price")

    assert "1 rewritten" in proposal["message"]
    # The old dead-end reply must not be what the customer sees.
    assert "I couldn’t apply that change" not in proposal["message"]


def test_proposal_reports_added_and_removed_statements() -> None:
    current = _text_brief()
    proposed = current.model_copy(deep=True)
    proposed.categories[0].elements.append(
        ElementBrief(
            name="Skip the haggling — your price is on the tag.",
            element_type="text",
            content="Skip the haggling — your price is on the tag.",
        )
    )

    proposal = StudyBriefService._regeneration_proposal(
        current, proposed, ["categories"]
    )
    preview = proposal["preview"]

    assert preview["summary"] == "1 added"
    assert preview["items"][0]["type"] == "added"
    assert preview["items"][0]["before"] == ""
    assert preview["items"][0]["after"].startswith("Skip the haggling")
