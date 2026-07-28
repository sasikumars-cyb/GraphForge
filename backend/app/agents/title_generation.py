"""AI-generated concise titles for workflows and standalone runs.

A single cheap LLM call turns a full objective/prompt (which can be
thousands of characters — NewWorkflowPage's textarea invites a full
multi-paragraph brief) into a 5-10 word human-readable title. Falls back
to a word-boundary-truncated version of the objective on any failure —
title generation never blocks workflow/run creation.
"""

from __future__ import annotations

import logging

from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.ai.providers.factory import create_llm_provider
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You generate short, human-readable titles for engineering tasks. "
    "Respond with ONLY the title text - 5 to 10 words, title case, no "
    "quotes, no markdown, no trailing punctuation, no commentary. "
    'Examples: "Refactor Authentication Module", "Review Payment Service PR", '
    '"Investigate CI Pipeline Failure".'
)

_FALLBACK_MAX_CHARS = 60


def _fallback_title(objective: str) -> str:
    """Word-boundary-truncated version of the objective — never cuts a
    word in half, never empty."""
    collapsed = " ".join(objective.split())  # collapse newlines/whitespace
    if not collapsed:
        return "Untitled"
    if len(collapsed) <= _FALLBACK_MAX_CHARS:
        return collapsed
    truncated = collapsed[:_FALLBACK_MAX_CHARS].rsplit(" ", 1)[0]
    return f"{truncated}…" if truncated else collapsed[:_FALLBACK_MAX_CHARS]


async def generate_title(objective: str, *, model: str | None = None) -> str:
    """Generate a concise title for `objective`.

    Always returns something usable — on any provider failure (not
    configured, rate limited, timeout, malformed response), logs a
    warning and falls back to a truncated version of the objective
    itself rather than raising and blocking workflow/run creation.
    """
    try:
        provider = create_llm_provider(model=model)
        response = await provider.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=objective,
            options=LLMRequestOptions(response_format=ResponseFormat.TEXT),
        )
        title = response.text.strip().strip('"').strip()
        if title:
            return title
        logger.warning("title_generation_empty_response falling back to truncated objective")
    except AppError as exc:
        logger.warning("title_generation_failed error=%s falling back to truncated objective", exc)
    return _fallback_title(objective)
