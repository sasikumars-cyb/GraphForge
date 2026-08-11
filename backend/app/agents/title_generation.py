"""AI-generated concise titles for workflows and standalone runs.

A single cheap LLM call turns a full objective/prompt (which can be
thousands of characters — NewWorkflowPage's textarea invites a full
multi-paragraph brief) into a 5-10 word human-readable title. Falls back
to a word-boundary-truncated version of the objective on any failure —
title generation never blocks workflow/run creation.
"""

from __future__ import annotations

import logging
import re

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


# UX audit P1.5: two confirmed, concrete title bugs.
#
# 1. Repository Understanding — a read-only, no-code-change agent — got
#    titled "Refactor Payment Service Java Module": the system prompt's own
#    examples above are all action-verb phrasings ("Refactor...",
#    "Review..."), so the model defaults to that framing even for an
#    agent whose objective is never "do X to the codebase", just "explain
#    what this repo does". Read-only analysis goals below skip the LLM
#    call entirely — there's no free-text task to summarize, only a
#    repository name, and no action verb can honestly describe them.
# 2. Documentation Health and API Intelligence, run against the same
#    repository, produced titles indistinguishable from one another in Run
#    History ("Review Order Service Python Code" vs. "...Repository") —
#    neither one's title said which agent produced it. Every title below
#    is deterministically prefixed with its agent label, so this can't
#    happen for any goal, LLM-generated or not.
_AGENT_LABEL_BY_GOAL: dict[str, str] = {
    "plan_freeform": "Planning",
    "develop_change_plan": "Development",
    "plan_tests": "Testing",
    "review_pr": "PR Review",
    "analyze_documentation_health": "Documentation Health",
    "analyze_api_intelligence": "API Intelligence",
    "analyze_repository_understanding": "Repository Understanding",
}

# These goals' "objective" is always a bare repository full_name (e.g.
# "org/order-service-python") — read-only inspection, never a change — so
# their title is built deterministically from that name instead of asking
# an LLM to invent an engineering-task-shaped summary for something that
# isn't one.
_READ_ONLY_REPOSITORY_GOALS = frozenset(
    {
        "analyze_documentation_health",
        "analyze_api_intelligence",
        "analyze_repository_understanding",
    }
)


def _agent_prefixed(goal: str | None, title: str) -> str:
    label = _AGENT_LABEL_BY_GOAL.get(goal or "")
    return f"{label} — {title}" if label else title


def _repository_short_name(full_name: str) -> str:
    """ "org/order-service-python" -> "Order Service Python" — the
    repository's own name, human-cased, with no owner/org prefix and no
    action verb invented on top of it."""
    name = full_name.rsplit("/", 1)[-1]
    words = re.split(r"[-_\s]+", name)
    return " ".join(w.capitalize() if w.islower() or w.isupper() else w for w in words if w)


def fallback_title(objective: str) -> str:
    """Word-boundary-truncated version of the objective — never cuts a
    word in half, never empty.

    Public (not `_fallback_title`) because `workflow_service.create_workflow`
    uses this as the *initial* synchronous title — instant and free — before
    handing the real AI title generation to a background task (see
    `app.orchestrator.background_execution.schedule_title_generation`).
    `generate_title` below still falls back to this same function on
    failure, so a workflow's title is never worse than what it would have
    shown while waiting synchronously before this change.
    """
    collapsed = " ".join(objective.split())  # collapse newlines/whitespace
    if not collapsed:
        return "Untitled"
    if len(collapsed) <= _FALLBACK_MAX_CHARS:
        return collapsed
    truncated = collapsed[:_FALLBACK_MAX_CHARS].rsplit(" ", 1)[0]
    return f"{truncated}…" if truncated else collapsed[:_FALLBACK_MAX_CHARS]


async def generate_title(
    objective: str, *, model: str | None = None, goal: str | None = None
) -> str:
    """Generate a concise title for `objective`.

    Always returns something usable — on any provider failure (not
    configured, rate limited, timeout, malformed response), logs a
    warning and falls back to a truncated version of the objective
    itself rather than raising and blocking workflow/run creation.

    `goal` (optional — every existing caller that omits it gets exactly the
    prior behavior) drives two UX-audit fixes (P1.5): read-only,
    repository-scoped goals never reach the LLM at all (see
    `_READ_ONLY_REPOSITORY_GOALS`'s own docstring for why), and every
    result — LLM-generated or fallback — gets its agent label prefixed on,
    so two different agents run against the same repository never produce
    indistinguishable titles.
    """
    if goal in _READ_ONLY_REPOSITORY_GOALS:
        return _agent_prefixed(goal, _repository_short_name(objective))
    try:
        provider = StageAwareLLMProvider(stage=None, model=model)
        response = await provider.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=objective,
            options=LLMRequestOptions(response_format=ResponseFormat.TEXT),
        )
        title = response.text.strip().strip('"').strip()
        if _looks_like_a_title(title):
            return _agent_prefixed(goal, title)
        logger.warning(
            "title_generation_unusable_response falling back to truncated objective: %.100r",
            title,
        )
    except AppError as exc:
        logger.warning("title_generation_failed error=%s falling back to truncated objective", exc)
    return _agent_prefixed(goal, fallback_title(objective))
