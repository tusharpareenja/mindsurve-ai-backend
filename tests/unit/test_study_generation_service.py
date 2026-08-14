"""Study generation service tests with mocked Unilever client."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import AppError
from app.db.models.study_generation_run import StudyGenerationRun
from app.schemas.study_brief import (
    AudienceBrief,
    CategoryBrief,
    ClassificationQuestionBrief,
    ElementBrief,
    RatingScaleBrief,
    StudyBrief,
    StudyBriefUpdate,
)
from app.services.study_generation_service import StudyGenerationService
from app.services.study_payload import fingerprint_brief


def _brief(study_id=None) -> StudyBrief:
    return StudyBrief(
        title="Test Study",
        background="Background for testing.",
        language="en",
        study_type="grid",
        main_question="How appealing is this?",
        orientation_text="Rate each image.",
        rating_scale=RatingScaleBrief(min_label="Low", max_label="High"),
        categories=[
            CategoryBrief(
                name="Aura",
                elements=[
                    ElementBrief(
                        name="Aura.1",
                        element_type="image",
                        content="https://cdn.example/a.png",
                    ),
                    ElementBrief(
                        name="Aura.2",
                        element_type="image",
                        content="https://cdn.example/b.png",
                    ),
                ],
            ),
            CategoryBrief(
                name="Garden",
                elements=[
                    ElementBrief(
                        name="Garden.1",
                        element_type="image",
                        content="https://cdn.example/c.png",
                    ),
                    ElementBrief(
                        name="Garden.2",
                        element_type="image",
                        content="https://cdn.example/d.png",
                    ),
                ],
            ),
        ],
        classification_questions=[
            ClassificationQuestionBrief(
                question_text="Q1?", options=["A", "B"]
            ),
            ClassificationQuestionBrief(
                question_text="Q2?", options=["A", "B"]
            ),
            ClassificationQuestionBrief(
                question_text="Q3?", options=["A", "B"]
            ),
        ],
        audience=AudienceBrief(
            number_of_respondents=10,
            age_segments=["18-24"],
            age_distribution={"18-24": 100},
            countries=["India"],
            gender_male=50,
            gender_female=50,
        ),
        status="created",
        study_id=study_id,
    )


@pytest.fixture
def service():
    db = MagicMock()
    svc = StudyGenerationService(db)
    return svc


def test_launch_rejects_stale_fingerprint(service):
    user = MagicMock()
    user.id = uuid4()
    chat_id = uuid4()
    study_id = uuid4()
    brief = _brief(study_id)

    run = StudyGenerationRun(
        id=uuid4(),
        chat_id=chat_id,
        project_id=uuid4(),
        user_id=user.id,
        study_id=study_id,
        revision=1,
        status="ready",
        progress=100,
        message="ready",
        fingerprint="stale-fingerprint",
        study_status="draft",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    service._owned_ready_chat = MagicMock(return_value=(MagicMock(), MagicMock(), brief))
    service.repo.latest_for_chat = MagicMock(return_value=run)

    with pytest.raises(AppError) as exc:
        service.launch(user, chat_id, access_token="tok")
    assert "Regenerate" in exc.value.message or "changed" in exc.value.message.lower()


def test_idempotent_start_resumes_active_run(service):
    user = MagicMock()
    user.id = uuid4()
    chat_id = uuid4()
    study_id = uuid4()
    brief = _brief(study_id)
    chat = MagicMock()
    chat.id = chat_id
    project = MagicMock()
    project.id = uuid4()

    active = StudyGenerationRun(
        id=uuid4(),
        chat_id=chat_id,
        project_id=project.id,
        user_id=user.id,
        study_id=study_id,
        upstream_job_id="job-1",
        revision=1,
        status="generating",
        progress=40,
        message="working",
        fingerprint=fingerprint_brief(brief),
        study_status="draft",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    service._owned_ready_chat = MagicMock(return_value=(chat, project, brief))
    service.repo.active_for_chat = MagicMock(return_value=active)
    service._refresh_from_upstream = MagicMock()

    with patch("app.services.study_generation_service.UnileverClient") as Client:
        Client.return_value.job_websocket_url.return_value = "ws://example/ws"
        result = service.start(user, chat_id, access_token="tok")

    assert result.resumed is True
    assert result.run.id == active.id
    service._refresh_from_upstream.assert_called_once()


def test_start_resumes_launched_run_instead_of_regenerating(service):
    user = MagicMock()
    user.id = uuid4()
    chat_id = uuid4()
    study_id = uuid4()
    brief = _brief(study_id)
    chat = MagicMock()
    chat.id = chat_id
    project = MagicMock()
    project.id = uuid4()

    launched = StudyGenerationRun(
        id=uuid4(),
        chat_id=chat_id,
        project_id=project.id,
        user_id=user.id,
        study_id=study_id,
        revision=1,
        status="launched",
        progress=100,
        message="live",
        fingerprint="anything",
        study_status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    service._owned_ready_chat = MagicMock(return_value=(chat, project, brief))
    service.repo.active_for_chat = MagicMock(return_value=None)
    service.repo.latest_for_chat = MagicMock(return_value=launched)

    with patch("app.services.study_generation_service.UnileverClient") as Client:
        Client.return_value.job_websocket_url.return_value = None
        result = service.start(user, chat_id, access_token="tok")

    assert result.resumed is True
    assert result.run.id == launched.id
    assert result.run.status == "launched"


def test_start_fails_without_study_id(service):
    user = MagicMock()
    user.id = uuid4()
    brief = _brief(None)
    service._owned_ready_chat = MagicMock(
        return_value=(MagicMock(), MagicMock(), brief)
    )
    with pytest.raises(AppError) as exc:
        service.start(user, uuid4(), access_token="tok")
    assert exc.value.status_code == 422


def test_statement_change_requires_confirmation_before_regeneration(service):
    user = MagicMock()
    user.id = uuid4()
    chat_id = uuid4()
    study_id = uuid4()
    brief = _brief(study_id)
    chat = MagicMock()
    chat.id = chat_id
    chat.project_id = uuid4()

    ready = StudyGenerationRun(
        id=uuid4(),
        chat_id=chat_id,
        project_id=chat.project_id,
        user_id=user.id,
        study_id=study_id,
        revision=1,
        status="ready",
        progress=100,
        message="ready",
        fingerprint=fingerprint_brief(brief),
        study_status="draft",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    changed_categories = brief.model_copy(deep=True).categories
    changed_categories[0].elements[0].name = "Updated statement"
    patch = StudyBriefUpdate(categories=changed_categories)

    service._owned_chat_brief = MagicMock(
        return_value=(chat, MagicMock(), brief)
    )
    service.repo.latest_for_chat = MagicMock(return_value=ready)
    service.projects.save_chat = MagicMock()

    preview = service.preview_brief_changes(user, chat_id, patch)
    assert preview.requires_regeneration is True
    assert "categories" in preview.changed_fields
    assert "not been applied" in preview.message

    with pytest.raises(AppError) as exc:
        service.apply_brief_and_regenerate(
            user,
            chat_id,
            patch,
            access_token="tok",
            confirm_regeneration=False,
        )

    assert exc.value.status_code == 409
    assert "Confirm" in exc.value.message
    service.projects.save_chat.assert_not_called()


def test_question_and_orientation_changes_do_not_require_regeneration(service):
    user = MagicMock()
    user.id = uuid4()
    chat_id = uuid4()
    study_id = uuid4()
    brief = _brief(study_id)
    chat = MagicMock()
    chat.id = chat_id

    ready = StudyGenerationRun(
        id=uuid4(),
        chat_id=chat_id,
        project_id=uuid4(),
        user_id=user.id,
        study_id=study_id,
        revision=1,
        status="ready",
        progress=100,
        message="ready",
        fingerprint=fingerprint_brief(brief),
        study_status="draft",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    patch = StudyBriefUpdate(
        main_question="How relevant is this?",
        orientation_text="Read each item and rate it.",
        classification_questions=[
            ClassificationQuestionBrief(
                question_text="Updated screener?", options=["Yes", "No"]
            )
        ],
    )

    service._owned_chat_brief = MagicMock(
        return_value=(chat, MagicMock(), brief)
    )
    service.repo.latest_for_chat = MagicMock(return_value=ready)

    preview = service.preview_brief_changes(user, chat_id, patch)
    assert preview.requires_regeneration is False
    assert preview.changed_fields == []
