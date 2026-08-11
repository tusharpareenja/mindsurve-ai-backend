"""Thin OpenAI chat client used by MindSurve AI services."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


def openai_configured() -> bool:
    settings = get_settings()
    return bool(settings.OPENAI_API_KEY and settings.OPENAI_API_KEY.strip())


def chat_json(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4,
) -> dict[str, Any]:
    """Call the chat model and parse a JSON object response."""
    settings = get_settings()
    if not openai_configured():
        raise AppError(
            "AI is not configured. Set OPENAI_API_KEY to continue.",
            status_code=503,
        )

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AppError("AI client is unavailable.", status_code=503) from exc

    # Bound wait so proxies don't hang forever on large attachment turns.
    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=75.0)
    try:
        completion = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as exc:
        logger.exception("OpenAI chat completion failed")
        name = type(exc).__name__
        if "Timeout" in name or "timeout" in str(exc).lower():
            raise AppError(
                "The AI took too long to respond. Please try again — "
                "your uploads are already saved.",
                status_code=504,
            ) from None
        raise AppError(
            "The AI assistant is temporarily unavailable. Please try again.",
            status_code=502,
        ) from None


    content = completion.choices[0].message.content if completion.choices else None
    if not content:
        raise AppError("The AI returned an empty response. Please try again.", status_code=502)

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.error("OpenAI returned non-JSON: %s", content[:500])
        raise AppError(
            "The AI returned an unexpected response. Please try again.",
            status_code=502,
        ) from None

    if not isinstance(data, dict):
        raise AppError(
            "The AI returned an unexpected response. Please try again.",
            status_code=502,
        )
    return data
