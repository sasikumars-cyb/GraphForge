"""Confluence Tool — REST (CQL search) and MCP, same two-transport shape
as JiraTool.

REST search uses Confluence's CQL (Confluence Query Language) `/wiki/rest/
api/search` endpoint — the same one Confluence's own UI search bar uses
under the hood. A bare text query is escaped into `text ~ "query*"`, the
same JQL-injection-shaped risk (and the same fix — backslash then
double-quote escaping) `JiraTool.search_issues` already guards against, so
this reuses that exact escaping approach rather than inventing a second
one. Content type is left unrestricted by default (pages, blog posts, and
attachments in Confluence's own default CQL search all match plain text
search) — page titles/labels are how Confluence itself distinguishes
design docs from ADRs from runbooks, not a fixed field this API exposes
generically, so this searches all of them and lets the query terms (or an
explicit space/label filter, see `search()`) narrow results — same
"the caller's query terms are what filters this" approach `search_issues`
already takes for Jira.

MCP path is unchanged from before this refactor: tool/argument names are
configurable, not hardcoded, because they are defined by whichever MCP
server is actually deployed. Transport selection follows the same policy
as Jira: if MCP is configured, attempt it first (an auto-wired MCP
endpoint may not actually be reachable/compatible — see
app.tools.setup's registration-metadata-driven auto-wire), then fall back
to REST on any *recoverable* MCP failure (unreachable server, wrong
tool/argument names, a non-auth error) rather than only on "not
configured". A fatal auth failure on a real (not auto-wired) MCP
connection with no REST fallback available still surfaces as a failure —
never silently swallowed.

Limitation (documented per Part 5's "if a capability cannot be
implemented through REST, document it explicitly" instruction): CQL search
returns an `excerpt` (a search-relevance snippet), not the full page body.
Fetching a page's full body would need a second REST call per result
(`/wiki/rest/api/content/{id}?expand=body.view`) — omitted here to keep
this at parity with the MCP path's own snippet-only results and to bound
the number of requests one `execute()` call makes; the snippet is enough
to ground a plan in whether a relevant doc/ADR/runbook exists, which is
this tool's purpose (see class docstring).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.tools.interfaces import (
    ToolCategory,
    ToolHealth,
    ToolInput,
    ToolResult,
)
from app.tools.mcp_support import MCPToolError, call_mcp_tool

logger = logging.getLogger(__name__)


class ConfluenceApiError(Exception):
    """Raised internally when Confluence's REST API returns an error
    response — mirrors JiraApiError's role in jira_tool.py."""


def _escape_cql_text(text: str) -> str:
    """Escape a free-text query for embedding in a CQL string literal.
    Same rule as JQL (backslash, then double-quote) — both are C-like
    query languages with the same string-literal escaping convention."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


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

    def __init__(self, config: dict[str, Any]) -> None:
        # REST transport config.
        self._base_url: str = config.get("confluence_base_url", "").rstrip("/")
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
        return bool(self._base_url and self._email and self._token)

    def requires_auth(self) -> bool:
        return True

    async def execute(self, input: ToolInput) -> ToolResult:
        if not self._uses_mcp and not self._uses_rest:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="Confluence is not configured (missing base URL/email/API token, "
                "or an MCP server URL).",
            )

        if self._uses_mcp:
            result = await self._execute_via_mcp(input.query)
            # Same reasoning as JiraTool.execute(): an auto-wired MCP
            # endpoint may not actually be reachable/compatible, so a
            # recoverable MCP failure falls back to REST when available
            # rather than failing the whole tool call.
            if result.success or not self._uses_rest:
                return result
            logger.info("confluence_tool_mcp_fallback_to_rest query=%.80s", input.query)
            return await self._execute_via_rest(input.query)
        return await self._execute_via_rest(input.query)

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

        return self._build_result(query, pages)

    async def _execute_via_rest(self, query: str) -> ToolResult:
        if not query.strip():
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="Empty Confluence search query.",
            )

        cql = f'text ~ "{_escape_cql_text(query.strip())}*" ORDER BY lastmodified DESC'
        try:
            async with httpx.AsyncClient(
                auth=(self._email, self._token), timeout=10.0, follow_redirects=True
            ) as client:
                response = await client.get(
                    f"{self._base_url}/wiki/rest/api/search",
                    params={"cql": cql, "limit": 5, "excerpt": "highlight"},
                    headers={"Accept": "application/json"},
                )
            if response.status_code == 401:
                raise ConfluenceApiError("Confluence authentication failed — check email/API token.")
            if response.status_code == 400:
                raise ConfluenceApiError(f"Confluence rejected the search query: {response.text[:200]}")
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type:
                raise ConfluenceApiError(
                    f"Confluence returned a non-JSON response (content-type: "
                    f"{content_type or 'none'}, status: {response.status_code}) — check the "
                    f"base URL is correct (e.g. https://yourorg.atlassian.net, no trailing path)."
                )
            payload = response.json()
        except ConfluenceApiError as exc:
            logger.warning("confluence_tool_rest_failed query=%.80s error=%s", query, str(exc))
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=str(exc),
            )
        except httpx.HTTPError as exc:
            logger.warning("confluence_tool_http_error query=%.80s error=%s", query, str(exc))
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=f"Confluence request failed: {exc}",
            )

        pages = []
        for item in payload.get("results", [])[:5]:
            content = item.get("content") or {}
            title = str(item.get("title") or content.get("title") or "")
            excerpt = str(item.get("excerpt") or "")
            relative_url = str(item.get("url") or content.get("_links", {}).get("webui") or "")
            url = f"{self._base_url}{relative_url}" if relative_url else ""
            pages.append({"title": title, "excerpt": excerpt, "url": url})

        return self._build_result(query, pages)

    def _build_result(self, query: str, pages: list[dict[str, str]]) -> ToolResult:
        """Shared result shape for both transports — same ToolResult either
        way, so nothing downstream needs to know or care which one ran."""
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
            health = await self._health_check_mcp()
            if health in (ToolHealth.HEALTHY, ToolHealth.RATE_LIMITED) or not self._uses_rest:
                return health
            return await self._health_check_rest()
        return await self._health_check_rest()

    async def _health_check_mcp(self) -> ToolHealth:
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
            # "You don't have permission to connect via API token" for an
            # org that hasn't enabled that access — no "auth"/401/403
            # substring, so this needs its own check, not just status codes.
            text = str(exc).lower()
            if "auth" in text or "401" in text or "403" in text or "permission" in text:
                return ToolHealth.AUTH_FAILED
            return ToolHealth.OFFLINE

    async def _health_check_rest(self) -> ToolHealth:
        if not self._base_url or not self._email or not self._token:
            return ToolHealth.UNCONFIGURED
        try:
            async with httpx.AsyncClient(
                auth=(self._email, self._token), timeout=5.0, follow_redirects=True
            ) as client:
                response = await client.get(f"{self._base_url}/wiki/rest/api/space", params={"limit": 1})
            if response.status_code == 401:
                return ToolHealth.AUTH_FAILED
            if response.status_code >= 500:
                return ToolHealth.UNAVAILABLE
            if response.status_code >= 400:
                return ToolHealth.OFFLINE
            if "application/json" not in response.headers.get("content-type", ""):
                return ToolHealth.OFFLINE
            return ToolHealth.HEALTHY
        except httpx.HTTPError:
            return ToolHealth.OFFLINE
