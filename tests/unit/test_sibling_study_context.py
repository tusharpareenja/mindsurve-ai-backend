"""Sibling chat study summaries for shared project context."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.schemas.study_brief import (
    AudienceBrief,
    ClassificationQuestionBrief,
    RatingScaleBrief,
    StudyBrief,
)
from app.services.study_brief_service import StudyBriefService


class _FakeRepo:
    def __init__(self, chats: list[SimpleNamespace]) -> None:
        self._chats = chats

    def list_chats_for_project(self, project_id):  # noqa: ANN001
        return [c for c in self._chats if c.project_id == project_id]


def test_sibling_summaries_exclude_current_and_include_brief() -> None:
    project_id = uuid4()
    current_id = uuid4()
    other_id = uuid4()
    other_brief = StudyBrief(
        title="Visual Response Study",
        background="Background text",
        study_type="grid",
        main_question="How appealing?",
        orientation_text="Rate images.",
        rating_scale=RatingScaleBrief(min_label="Low", max_label="High"),
        classification_questions=[
            ClassificationQuestionBrief(
                question_text="Do you use social media?",
                options=["Yes", "No", "Sometimes"],
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
        status="ready",
    )
    chats = [
        SimpleNamespace(
            id=current_id,
            project_id=project_id,
            title="Current chat",
            study_brief={},
        ),
        SimpleNamespace(
            id=other_id,
            project_id=project_id,
            title="Visual Response Study Setup",
            study_brief=other_brief.model_dump(mode="json"),
        ),
    ]
    service = StudyBriefService(db=SimpleNamespace())  # type: ignore[arg-type]
    service.repo = _FakeRepo(chats)  # type: ignore[assignment]

    summaries = service._sibling_study_summaries(project_id, current_id)
    assert len(summaries) == 1
    assert summaries[0]["chat_id"] == str(other_id)
    assert summaries[0]["title"] == "Visual Response Study"
    assert summaries[0]["full_brief"]["title"] == "Visual Response Study"
    assert len(summaries[0]["classification_questions"]) == 1


def _service() -> StudyBriefService:
    return StudyBriefService(db=SimpleNamespace())  # type: ignore[arg-type]


def _sibling(**over) -> dict:
    base = {
        "chat_id": str(uuid4()),
        "chat_title": "Setup chat",
        "title": "Visual Response Study",
        "brief_status": "created",
        "study_id": str(uuid4()),
        "preview_url": "https://mindsurve.com/home/create-study/preview?studyId=abc",
        "share_url": None,
        "generation": {"launched": False},
        "collection": None,
        "full_brief": {"title": "Visual Response Study"},
    }
    base.update(over)
    return base


def test_answer_share_url_when_live() -> None:
    service = _service()
    sib = _sibling(share_url="https://mindsurve.com/participate/xyz")
    answer = service._answer_sibling_question("give me the share url", [sib])
    assert answer is not None
    assert "https://mindsurve.com/participate/xyz" in answer


def test_answer_preview_url_when_not_live() -> None:
    service = _service()
    sib = _sibling(share_url=None)
    answer = service._answer_sibling_question(
        "visual response study can you give this study share url", [sib]
    )
    assert answer is not None
    assert "preview" in answer.lower()
    assert sib["preview_url"] in answer


def test_answer_list_studies() -> None:
    service = _service()
    answer = service._answer_sibling_question(
        "can you list all studies in this project",
        [_sibling(title="Study A"), _sibling(title="Study B")],
    )
    assert answer is not None
    assert "Study A" in answer and "Study B" in answer


def test_answer_response_counts() -> None:
    service = _service()
    sib = _sibling(
        collection={
            "status": "completed",
            "completed": 10,
            "respondents_requested": 10,
            "total_responses": 10,
        }
    )
    answer = service._answer_sibling_question("did my responses complete", [sib])
    assert answer is not None
    assert "complete" in answer.lower()
    assert "10" in answer


def test_info_question_does_not_overwrite_brief() -> None:
    """Asking about a sibling must return intent=answer and leave the brief untouched."""
    service = _service()
    current = StudyBrief(title="My current draft")
    sib = _sibling(share_url="https://mindsurve.com/participate/xyz")
    payload = service._heuristic_ai(
        current, "give me the share url for visual response study", [], siblings=[sib]
    )
    assert payload["intent"] == "answer"
    assert payload["study_brief"]["title"] == "My current draft"
    assert service._resolve_intent(payload, has_attachments=False) == "answer"


def test_reuse_request_copies_sibling_brief() -> None:
    service = _service()
    current = StudyBrief()
    full = StudyBrief(title="Visual Response Study", study_type="grid").model_dump(
        mode="json"
    )
    sib = _sibling(title="Visual Response Study", full_brief=full)
    payload = service._heuristic_ai(
        current, "continue with visual response study here", [], siblings=[sib]
    )
    assert payload["intent"] == "copy_sibling"
    assert payload["study_brief"]["title"] == "Visual Response Study"
