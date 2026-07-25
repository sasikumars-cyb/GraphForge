"""GitHub Tool — stub implementation.

Currently returns an UNCONFIGURED health status. Wired up by providing
a GitHub API token via the Tool Registry settings UI.
"""

from __future__ import annotations

import logging

from app.tools.interfaces import (
    ITool,
    ToolCategory,
    ToolHealth,
    ToolInput,
    ToolResult,
)

logger = logging.getLogger(__name__)


class GitHubTool:
    """Fetches PR metadata, open issues, and repository activity from GitHub."""

    tool_id = "github"
    display_name = "GitHub"
    description = (
        "Fetches pull request metadata, open issues, and repository activity "
        "from the GitHub API. Used by the Planning Agent to assess recent code "
        "changes and active workstreams."
    )
    category = ToolCategory.CODE_INTELLIGENCE
    capabilities = [
        "pull_requests",
        "issues",
        "repository_activity",
        "code_reviews",
    ]

    def __init__(self, config: dict) -> None:
        self._token: str = config.get("github_token", "")
        self._base_url: str = config.get("github_api_url", "https://api.github.com")

    def requires_auth(self) -> bool:
        return True

    async def execute(self, input: ToolInput) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=False,
            error="GitHub tool is not yet implemented.",
        )

    async def health_check(self) -> ToolHealth:
        if not self._token:
            return ToolHealth.UNCONFIGURED
        return ToolHealth.HEALTHY
