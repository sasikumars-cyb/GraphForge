"""Prompt-template rendering shared by the freeform-JSON agents (Planning,
Development, Testing, Engineering Review, Code Generation).

Pure string manipulation — no network, no provider coupling. Previously
lived in app.agents._llm alongside the (now-removed) duplicate HTTP
transport; moved here on its own since it has nothing to do with talking
to an LLM provider.
"""

from __future__ import annotations

import re
from pathlib import Path


def render_prompt_template(
    template_path: Path, task_description: str, graph_context: str, max_graph_context_chars: int
) -> str:
    """Strip YAML front-matter and substitute the two template variables
    every freeform-JSON agent prompt uses."""
    raw = template_path.read_text(encoding="utf-8")
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", raw, flags=re.DOTALL)
    body = body.replace("{{ task_description }}", task_description)
    body = body.replace("{{ graph_context }}", graph_context[:max_graph_context_chars])
    return body
