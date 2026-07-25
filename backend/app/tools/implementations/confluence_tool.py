"""Confluence Tool — stub implementation.

Currently returns UNCONFIGURED health status. Activated by providing
a Confluence base URL + API token via the Tool Registry settings UI.
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


class ConfluenceTool:
    """Fetches design documents, ADRs, and runbooks from Confluence."""

    tool_id = "confluence"
    display_name = "Confluence"
    description = (
        "Fetches design documents, ADRs, and runbooks from Confluence. Used by "
        "the Planning Agent to ground plans in recorded architectural decisions "
        "and avoid re-implementing what is already designed."
    )
    category = ToolCategory.DOCUMENTATION
    capabilities = [
        "design_documents",
        "adrs",
        "runbooks",
        "documentation",
    ]

    def __init__(self, config: dict) -> None:
        self._base_url: str = config.get("confluence_base_url", "")
        self._token: str = config.get("confluence_api_token", "")
        self._email: str = config.get("confluence_email", "")

    def requires_auth(self) -> bool:
        return True

    async def execute(self, input: ToolInput) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=False,
            error="Confluence tool is not yet implemented.",
        )

    async def health_check(self) -> ToolHealth:
        if not self._base_url or not self._token:
            return ToolHealth.UNCONFIGURED
        return ToolHealth.HEALTHY
