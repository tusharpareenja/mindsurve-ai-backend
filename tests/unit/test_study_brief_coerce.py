"""LLM JSON mistakes must coerce into a valid StudyBrief, not be dropped."""

from app.ai_prompts.study_brief import (
    STUDY_BRIEF_SYSTEM_PROMPT,
    render_study_brief_user_prompt,
)
from app.schemas.study_brief import ClassificationQuestionBrief, StudyBrief


def test_user_prompt_render_ignores_literal_braces() -> None:
    prompt = render_study_brief_user_prompt(
        project_name="Dealership",
        project_id="abc",
        project_sibling_studies_json="[]",
        current_brief_json="{}",
        conversation_transcript="(empty)",
        user_message="hello",
        new_attachments_json="[]",
        document_excerpts="(none)",
    )
    assert "Dealership" in prompt
    assert "hello" in prompt
    assert "{user_message}" not in prompt


def test_system_prompt_includes_theme_style_rules_and_text_only_statements() -> None:
    prompt = STUDY_BRIEF_SYSTEM_PROMPT
    assert "TEXT STUDIES ONLY" in prompt
    assert "Describe a situation that is important to you personally" in prompt
    assert "strong love" in prompt
    assert "Apply ONLY the requested edits" in prompt
    assert "no more than 2 short paragraphs" in prompt
    assert "4–5 short paragraphs" in prompt


def test_object_options_coerce_to_strings() -> None:
    question = ClassificationQuestionBrief.model_validate(
        {
            "question_text": "Have you applied for auto credit in the last year?",
            "options": [{"id": "a", "text": "Yes"}, {"label": "No"}],
        }
    )
    assert question.options == ["Yes", "No"]


def test_overlong_background_is_clipped() -> None:
    brief = StudyBrief.model_validate(
        {
            "title": "Credit profile messaging",
            "background": "x" * 5000,
            "study_type": "text",
        }
    )
    assert len(brief.background) == 2000


def test_study_type_aliases_and_empty_study_id() -> None:
    brief = StudyBrief.model_validate(
        {
            "title": "Credit profile messaging",
            "study_type": "statements",
            "study_id": "pending",
            "status": "draft",
            "attachments": [{"filename": "no-url.pdf"}],
        }
    )
    assert brief.study_type == "text"
    assert brief.study_id is None
    assert brief.status == "gathering"
    assert brief.attachments == []


def test_messy_ai_payload_validates() -> None:
    brief = StudyBrief.model_validate(
        {
            "title": "T" * 300,
            "background": "A messaging study for credit profiles.",
            "study_type": "copy",
            "study_id": "",
            "audience": {"number_of_respondents": "100"},
            "categories": [
                {
                    "name": "C" * 150,
                    "elements": [
                        {
                            "name": "S" * 200,
                            "element_type": "statement",
                            "content": "Tired of credit jargon in every ad?",
                        }
                    ],
                }
            ],
            "classification_questions": [
                {
                    "question_text": "Q" * 600,
                    "options": [{"text": "Yes"}, "No"],
                }
            ],
        }
    )
    assert len(brief.title) == 255
    assert brief.study_type == "text"
    assert brief.study_id is None
    assert brief.audience.number_of_respondents == 100
    assert len(brief.categories[0].name) == 100
    assert len(brief.categories[0].elements[0].name) == 150
    assert brief.categories[0].elements[0].element_type == "text"
    assert len(brief.classification_questions[0].question_text) == 500
    assert brief.classification_questions[0].options == ["Yes", "No"]


def test_category_statements_alias_and_string_elements() -> None:
    brief = StudyBrief.model_validate(
        {
            "title": "Credit profile messaging",
            "study_type": "text",
            "categories": [
                {
                    "name": "Middle Credit",
                    "statements": [
                        "Get pre-qualified in minutes",
                        {"name": "No dealer runaround", "content": "No dealer runaround"},
                    ],
                },
                "Prime Messaging",
            ],
        }
    )
    assert [c.name for c in brief.categories] == ["Middle Credit", "Prime Messaging"]
    assert brief.categories[0].elements[0].content == "Get pre-qualified in minutes"
    assert brief.categories[0].elements[1].content == "No dealer runaround"


def test_question_aliases_coerce() -> None:
    question = ClassificationQuestionBrief.model_validate(
        {
            "question": "What is your credit profile?",
            "choices": [{"text": "Challenged"}, "Prime"],
        }
    )
    assert question.question_text == "What is your credit profile?"
    assert question.options == ["Challenged", "Prime"]


def test_overlay_keeps_gpt_classification_questions() -> None:
    from app.services.study_brief_service import StudyBriefService

    current = StudyBrief.model_validate(
        {
            "title": "Credit profile messaging",
            "study_type": "text",
            "classification_questions": [
                {"question_text": "What is your current credit profile?", "options": ["A", "B"]},
            ],
        }
    )
    payload = {
        "changed_fields": ["classification_questions"],
        "study_brief": {
            "classification_questions": [
                {
                    "question": "Have you bought a car in the last 2 years?",
                    "choices": ["Yes", "No"],
                },
                {
                    "question_text": "Who decides the vehicle purchase?",
                    "options": ["Me", "Partner", "Family"],
                },
            ]
        },
    }
    service = StudyBriefService.__new__(StudyBriefService)
    updated = service._overlay_gpt_fields(
        current, payload, ["classification_questions"]
    )
    texts = [q.question_text for q in updated.classification_questions]
    assert texts == [
        "Have you bought a car in the last 2 years?",
        "Who decides the vehicle purchase?",
    ]


def test_requested_category_count_uses_target_not_current() -> None:
    from app.services.study_brief_service import StudyBriefService

    parse = StudyBriefService._requested_category_count
    assert parse("why only 3 categories it has to be 4") == 4
    assert parse("i want to have 4 categories currently we have 3") == 4
    assert parse("i want 4 categories") == 4
    assert parse("make it five categories") == 5


def test_edit_request_is_not_treated_as_answer() -> None:
    from app.services.study_brief_service import StudyBriefService

    payload = {"intent": "answer", "study_brief": {}}
    assert (
        StudyBriefService._resolve_intent(
            payload,
            has_attachments=False,
            user_message="there is duplicate categories",
        )
        == "build"
    )
    assert (
        StudyBriefService._resolve_intent(
            payload,
            has_attachments=False,
            user_message="change the classification questions",
        )
        == "build"
    )
    assert (
        StudyBriefService._resolve_intent(
            payload,
            has_attachments=False,
            user_message="how many respondents do we have?",
        )
        == "answer"
    )
