"""Jira Tool — stub implementation.

Currently returns UNCONFIGURED health status. Activated by providing
a Jira base URL + API token via the Tool Registry settings UI.
"""

from __future__ import annotations

import logging

from app.tools.interfaces import (
    ToolCategory,
    ToolHealth,
    ToolInput,
    ToolResult,
)

logger = logging.getLogger(__name__)


class JiraTool:
    """Fetches issues, epics, and sprint metadata from Jira."""

    tool_id = "jira"
    display_name = "Jira"
    description = (
        "Fetches issues, epics, and sprint metadata from Jira. Used by the "
        "Planning Agent to align implementation plans with active tickets and "
        "upcoming sprint commitments."
    )
    category = ToolCategory.PROJECT_MANAGEMENT
    capabilities = [
        "issues",
        "epics",
        "sprints",
        "project_management",
    ]

    def __init__(self, config: dict) -> None:
        self._base_url: str = config.get("jira_base_url", "")
        self._token: str = config.get("jira_api_token", "")
        self._email: str = config.get("jira_email", "")

    def requires_auth(self) -> bool:
        return True

    async def execute(self, input: ToolInput) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=False,
            error="Jira tool is not yet implemented.",
        )

    async def health_check(self) -> ToolHealth:
        if not self._base_url or not self._token:
            return ToolHealth.UNCONFIGURED
        return ToolHealth.HEALTHY
