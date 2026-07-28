"""FreeText Entry Resolver — PW-5.

Resolves a free-text goal string into a Subject so the Planning Agent
has a typed, canonical entry point to act on.

This is a pure function (no DB, no HTTP) — it normalises the raw user
input into a stable Subject. The Planning Agent reads the Subject and
decides what graph queries to run based on its content.

Rule: this resolver MUST remain free of I/O. Any enrichment (graph
lookups, Jira/Confluence calls) belongs in the Planning Agent's own
tool-calling loop, not here.
"""

from __future__ import annotations

import hashlib
import re

from app.agents._contract import Subject

# ---------------------------------------------------------------------------
# Recognised intent patterns — closed vocabulary per AGENT_FRAMEWORK.md
# ---------------------------------------------------------------------------

_COMPONENT_PATTERN = re.compile(
    r"\b(service|component|module|api|endpoint|topic|consumer|producer)\b",
    re.IGNORECASE,
)
_REPOSITORY_PATTERN = re.compile(
    r"\b(repo(?:sitory)?|codebase|project)\b",
    re.IGNORECASE,
)
_IMPACT_PATTERN = re.compile(
    r"\b(impact|affect|depend|change|risk|break|blast radius)\b",
    re.IGNORECASE,
)
_PLAN_PATTERN = re.compile(
    r"\b(plan|design|architect|propose|story|feature|ticket|epic)\b",
    re.IGNORECASE,
)


def _stable_id(text: str) -> str:
    """Return a short, stable, collision-resistant id for free text.

    Uses the first 12 hex chars of a SHA-256 digest — stable across
    processes, deterministic for the same input, and short enough to
    fit in Subject.subject_id without truncation issues.
    """
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def resolve(text: str) -> Subject:
    """Resolve a free-text goal string into a Subject.

    Examples:
        resolve("Plan a new payment retry feature") ->
            Subject(subject_id="freetext:abc123...", subject_type="freetext",
                    graph_node_ids=[], display_name="Plan a new payment retry feature")

        resolve("What services depend on order-service?") ->
            Subject(subject_id="freetext:def456...", subject_type="freetext",
                    graph_node_ids=[], display_name="What services depend on order-service?")

    The subject_type is always "freetext" for this resolver. The display_name
    is the raw (stripped) input text, truncated to 256 chars to fit DB columns.
    The Planning Agent reads `subject.display_name` as its task description.

    Raises ValueError if text is empty or whitespace-only.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("Free-text input must not be empty.")

    subject_id = f"freetext:{_stable_id(stripped)}"
    display_name = stripped[:256]

    return Subject(
        subject_id=subject_id,
        subject_type="freetext",
        graph_node_ids=[],
        display_name=display_name,
    )
