"""`ResponseRenderer` — pure formatting over an already-built dict of
sections. No retrieval, no business logic: every value it renders was
already decided by the agent's `render_response` hook (which itself only
reads an `ExecutionResult` plus the LLM narrative dict — see
`base_frontier_agent.py`). This module never touches `AgentContext`, a
database, or a graph.

A "section" is `str` (rendered as a paragraph) or `list[str]` (rendered
as a bullet list) — the same two shapes `documentation_health`'s report
narrative already uses (`summary` vs `strengths`/`areas_for_improvement`/
`suggested_next_actions`), generalized so every Frontier agent shares one
rendering path instead of each hand-writing Markdown.
"""

from __future__ import annotations

import json

Section = str | list[str]


def _render_section_markdown(heading: str, value: Section) -> str:
    if isinstance(value, list):
        if not value:
            return ""
        bullets = "\n".join(f"- {item}" for item in value)
        return f"### {heading}\n\n{bullets}"
    if not value:
        return ""
    return f"### {heading}\n\n{value}"


def to_markdown(title: str, sections: dict[str, Section]) -> str:
    """`sections` insertion order is preserved (dicts are ordered) — the
    caller decides section order by the order it builds the dict, not
    this function."""
    blocks = [f"# {title}"]
    blocks.extend(
        rendered
        for heading, value in sections.items()
        if (rendered := _render_section_markdown(heading, value))
    )
    return "\n\n".join(blocks)


def to_json(sections: dict[str, Section]) -> str:
    return json.dumps(sections, sort_keys=True, indent=2)


def to_ui_sections(sections: dict[str, Section]) -> list[dict[str, object]]:
    """A UI-ready list of `{"heading": ..., "kind": "text"|"list",
    "content": ...}` objects — the shape a frontend can iterate over
    without inspecting Python types."""
    return [
        {
            "heading": heading,
            "kind": "list" if isinstance(value, list) else "text",
            "content": value,
        }
        for heading, value in sections.items()
        if value
    ]


def to_executive_summary(summary: str, key_points: list[str], *, max_key_points: int = 5) -> str:
    """A short, human-scannable digest — one paragraph plus up to
    `max_key_points` bullets. Deliberately not the same as `to_markdown`
    of the full sections dict: this is the "read this first" view, always
    small regardless of how much detail the full report carries."""
    if not key_points:
        return summary
    bullets = "\n".join(f"- {point}" for point in key_points[:max_key_points])
    return f"{summary}\n\n{bullets}"
