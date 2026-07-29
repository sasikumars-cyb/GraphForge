"""AI-generated concise titles for workflows and standalone runs.

A single cheap LLM call turns a full objective/prompt (which can be
thousands of characters — NewWorkflowPage's textarea invites a full
multi-paragraph brief) into a 5-10 word human-readable title. Falls back
to a word-boundary-truncated version of the objective on any failure —
title generation never blocks workflow/run creation.
"""

from __future__ import annotations

import logging

from app.agents.llm import StageAwareLLMProvider
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
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

# A real title is "5 to 10 words" per the system prompt above — generous
# upper bound for that, not a hard word count. Guards against the model
# ignoring the instruction and returning a full sentence instead of a
# title: seen in practice when `objective` is a bare URL/ticket reference
# the model has no way to resolve itself (it has no tools; only the
# Planning Agent that runs afterward actually fetches Jira content), and
# it responds with something like "I don't have access to external URLs
# or Jira tickets. Please share the ticket details..." — non-empty, so it
# would otherwise pass straight through and become the workflow's title
# verbatim. A single deterministic length check catches this regardless
# of the exact wording, rather than pattern-matching every possible
# refusal phrasing.
_MAX_TITLE_CHARS = 80


def _looks_like_a_title(text: str) -> bool:
    return bool(text) and len(text) <= _MAX_TITLE_CHARS and "\n" not in text


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
        provider = StageAwareLLMProvider(stage=None, model=model)
        response = await provider.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=objective,
            options=LLMRequestOptions(response_format=ResponseFormat.TEXT),
        )
        title = response.text.strip().strip('"').strip()
        if _looks_like_a_title(title):
            return title
        logger.warning(
            "title_generation_unusable_response falling back to truncated objective: %.100r",
            title,
        )
    except AppError as exc:
        logger.warning("title_generation_failed error=%s falling back to truncated objective", exc)
    return _fallback_title(objective)
