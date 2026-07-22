"""Prompt template loading and rendering.

Loads markdown templates from ``app/ai/prompts/``, extracts YAML
front-matter metadata (including ``version``), and renders Jinja2-style
``{{ variable }}`` placeholders with provided context values.

Makes no AI calls — purely string manipulation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_VERSION_RE = re.compile(r'^version:\s*["\']?([^"\']+)["\']?', re.MULTILINE)


class PromptBuilder:
    """Loads markdown prompt templates and renders them with variables.

    Templates live in ``app/ai/prompts/*.md`` and use ``{{ variable }}``
    placeholders (Python ``string.Template`` with a custom pattern).
    YAML front-matter at the top of each file carries metadata such as
    the prompt ``version``.
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._prompts_dir = prompts_dir or _PROMPTS_DIR

    def load(self, template_name: str) -> str:
        """Load a raw template by name (without ``.md`` extension)."""
        path = self._prompts_dir / f"{template_name}.md"
        return path.read_text(encoding="utf-8")

    def extract_version(self, template_name: str) -> str:
        """Extract the ``version`` field from the template's YAML front-matter."""
        raw = self.load(template_name)
        front_matter = _FRONT_MATTER_RE.match(raw)
        if not front_matter:
            return ""
        version_match = _VERSION_RE.search(front_matter.group(1))
        return version_match.group(1) if version_match else ""

    def render(self, template_name: str, variables: dict[str, Any]) -> str:
        """Render a template by substituting ``{{ key }}`` placeholders.

        Returns the rendered prompt with front-matter stripped.
        """
        raw = self.load(template_name)
        # Strip front-matter from the rendered output
        body = _FRONT_MATTER_RE.sub("", raw)
        # Replace {{ variable }} placeholders
        for key, value in variables.items():
            body = body.replace("{{ " + key + " }}", str(value))
        return body
