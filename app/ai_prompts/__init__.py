"""Central registry for LLM prompts used by MindSurve."""

from app.ai_prompts.study_brief import STUDY_BRIEF_SYSTEM_PROMPT, STUDY_BRIEF_USER_TEMPLATE

__all__ = [
    "STUDY_BRIEF_SYSTEM_PROMPT",
    "STUDY_BRIEF_USER_TEMPLATE",
]
