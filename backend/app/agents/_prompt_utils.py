"""Pure prompt-rendering utilities shared by freeform-JSON agents.

Extracted from ``_llm.py`` so that migrated agents (which no longer use the
legacy ``call_chat_completion_json`` transport) can still access prompt
template rendering without importing the old HTTP-call machinery.
"""

from __future__ import annotations

import re
from pathlib import Path


def render_prompt_template(
    template_path: Path,
    task_description: str,
    graph_context: str,
    max_graph_context_chars: int,
) -> str:
    """Strip YAML front-matter and substitute the two template variables
    every freeform-JSON agent prompt uses."""
    raw = template_path.read_text(encoding="utf-8")
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", raw, flags=re.DOTALL)
    body = body.replace("{{ task_description }}", task_description)
    body = body.replace("{{ graph_context }}", graph_context[:max_graph_context_chars])
    return body
