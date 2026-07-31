"""Confirms the Confluence discovery loop's system prompt actually names
the specific engineering-knowledge categories the redesign asked for
(architecture, design decisions, known limitations, migration strategy,
standards/patterns, operational constraints) and instructs merging
overlapping pages — a guard against a future edit silently reverting to
a generic "follow what looks relevant" instruction with no categories
and no merge guidance.
"""

from __future__ import annotations

from app.agents.planning.confluence_context import _SYSTEM_PROMPT


def test_prompt_names_every_target_knowledge_category():
    prompt = _SYSTEM_PROMPT.lower()
    for category in (
        "architecture",
        "design decisions",
        "known limitations",
        "migration strategy",
        "standards",
        "operational constraints",
    ):
        assert category in prompt, f"expected the prompt to mention {category!r}"


def test_prompt_instructs_merging_overlapping_pages_not_listing_each_separately():
    prompt = _SYSTEM_PROMPT.lower()
    assert "merge" in prompt
    assert "engineering understanding" in prompt


def test_prompt_still_instructs_stopping_rather_than_over_exploring():
    # The redesign's precision-over-recall principle must survive this
    # change — adding target categories must not turn this into "fetch
    # everything that could possibly relate to any of these".
    prompt = _SYSTEM_PROMPT.lower()
    assert "not worth fetching" in prompt or "stop" in prompt
