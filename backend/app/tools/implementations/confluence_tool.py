"""Confluence Tool — MCP implementation; REST is still a stub.

Unlike Jira, there is no plain REST call implemented here yet — Confluence's
REST search API needs a query DSL (CQL) this tool doesn't build. What *is*
implemented is the MCP path, activated the same way as Jira's: reusing the
Knowledge Connection's REST credential as the MCP bearer token against a
known or operator-supplied MCP server (see app.tools.setup's
sync_knowledge_connection_to_tool and app.core.config's
confluence_mcp_default_server_url). Tool/argument names are configurable,
not hardcoded, because they are defined by whichever MCP server is actually
deployed — there is no single standard tool schema for "search Confluence"
across servers yet. Defaults below are a best guess at Atlassian's own
remote MCP server's naming as of writing; check your server's tool list (or
its docs) and override via config if it differs.
"""

from __future__ import annotations

import logging

from app.tools.interfaces import (
    ToolCategory,
    ToolHealth,
    ToolInput,
    ToolResult,
)
from app.tools.mcp_support import MCPToolError, call_mcp_tool

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
        # REST transport config — stored and health-checked, but execute()
        # has no REST path yet (see module docstring).
        self._base_url: str = config.get("confluence_base_url", "")
        self._token: str = config.get("confluence_api_token", "")
        self._email: str = config.get("confluence_email", "")

        # MCP transport config.
        self._mcp_server_url: str = config.get("confluence_mcp_server_url", "").rstrip("/")
        self._mcp_auth_token: str = config.get("confluence_mcp_api_key", "")
        self._mcp_tool_name: str = config.get("confluence_mcp_tool_name", "search")
        self._mcp_query_arg: str = config.get("confluence_mcp_query_arg", "query")

    @property
    def _uses_mcp(self) -> bool:
        return bool(self._mcp_server_url)

    @property
    def _uses_rest(self) -> bool:
        return bool(self._base_url and self._token)

    def requires_auth(self) -> bool:
        return True

    async def execute(self, input: ToolInput) -> ToolResult:
        if self._uses_mcp:
            return await self._execute_via_mcp(input.query)

        if not self._uses_rest:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="Confluence is not configured (missing base URL/API token, "
                "or an MCP server URL).",
            )
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=False,
            error="Confluence REST search is not yet implemented. Configure an "
            "MCP server to use this tool.",
        )

    async def _execute_via_mcp(self, query: str) -> ToolResult:
        try:
            payload = await call_mcp_tool(
                self._mcp_server_url,
                self._mcp_tool_name,
                {self._mcp_query_arg: query},
                auth_token=self._mcp_auth_token or None,
            )
        except MCPToolError as exc:
            logger.warning("confluence_tool_mcp_failed error=%s", str(exc))
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=str(exc),
            )

        # Response shape isn't standardized across MCP servers — this reads
        # the common "list of pages/results" shape with a couple of
        # fallbacks. If your server's shape differs, this is the one place
        # to adjust it.
        results = payload.get("results") or payload.get("pages") or payload.get("content") or []
        if isinstance(results, dict):
            results = [results]

        pages = []
        for item in results[:5]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "")
            excerpt = str(item.get("excerpt") or item.get("summary") or item.get("text") or "")
            url = str(item.get("url") or item.get("_links", {}).get("webui") or "")
            pages.append({"title": title, "excerpt": excerpt, "url": url})

        if not pages:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=True,
                data={"pages": [], "context_text": f"No Confluence pages found for: {query}"},
                summary="No matching Confluence pages found.",
                evidence_items=[],
            )

        context_lines = [f"Confluence search results for: {query}\n"]
        for p in pages:
            context_lines.append(f"- {p['title']}" + (f" ({p['url']})" if p["url"] else ""))
            if p["excerpt"]:
                context_lines.append(f"  {p['excerpt']}")
        context_text = "\n".join(context_lines)

        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={"pages": pages, "context_text": context_text},
            summary=f"Found {len(pages)} Confluence page(s) for: {query}",
            evidence_items=[context_text],
        )

    async def health_check(self) -> ToolHealth:
        if self._uses_mcp:
            try:
                await call_mcp_tool(
                    self._mcp_server_url,
                    self._mcp_tool_name,
                    {self._mcp_query_arg: "ping"},
                    auth_token=self._mcp_auth_token or None,
                    timeout=5.0,
                )
                return ToolHealth.HEALTHY
            except MCPToolError as exc:
                # e.g. Atlassian's own server reports a plain-language
                # "You don't have permission to connect via API token" for
                # an org that hasn't enabled that access — no "auth"/401/403
                # substring, so this needs its own check, not just status
                # codes. A non-auth failure here is a real OFFLINE
                # regardless of whether REST fields also happen to be
                # present — REST is a permanent stub (see module docstring),
                # so falling back to it would just misreport OFFLINE as
                # UNCONFIGURED, implying nothing was even attempted.
                text = str(exc).lower()
                if "auth" in text or "401" in text or "403" in text or "permission" in text:
                    return ToolHealth.AUTH_FAILED
                return ToolHealth.OFFLINE

        if not self._base_url or not self._token:
            return ToolHealth.UNCONFIGURED
        return ToolHealth.UNCONFIGURED  # REST path exists but is unimplemented (see execute()).
