"""Jira Tool — real implementation, REST or MCP.

Fetches a single issue's summary/description/status given either a bare
issue key ("NPT-6") or a full ticket URL
("https://myorg.atlassian.net/browse/NPT-6") anywhere in the tool's `query`
string. Used by the Planning Agent so a goal that references a Jira ticket
plans against the ticket's real, current content instead of the literal
URL string.

Two transports, one tool, one tool_id — this is the concrete case of the
architecture principle the Tool Registry exists for: the Planning Agent
calls `executor.execute("jira", ...)` either way and gets back the same
ToolResult shape. Which transport is active depends purely on which config
keys are present (see __init__): REST (Basic Auth: email + API token,
directly against Jira Cloud's REST API v3) if `jira_base_url`/`jira_email`/
`jira_api_token` are set, or MCP (against a Jira MCP server) if
`jira_mcp_server_url` is set. MCP takes priority when both happen to be
configured, since a Knowledge Connection is only ever created for one
transport at a time (see knowledge/registry.py's TransportSpec) — no
resolution logic is needed beyond "prefer MCP if its one required field is
present."
"""

from __future__ import annotations

import logging
import re
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

# Matches a Jira issue key (e.g. "NPT-6", "ABC-123") whether it's bare or
# trailing a /browse/ URL — same pattern either way, so one regex covers both.
_ISSUE_KEY_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def extract_issue_key(text: str) -> str | None:
    """Return the first Jira issue key found in free text, or None."""
    match = _ISSUE_KEY_PATTERN.search(text)
    return match.group(1) if match else None


def _adf_to_text(node: Any) -> str:
    """Flatten Jira's Atlassian Document Format (the JSON structure the
    Cloud API v3 returns for `description`) into plain text. Only handles
    the node types that actually appear in issue descriptions/comments —
    good enough for feeding an LLM planning prompt, not a full ADF renderer.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node

    node_type = node.get("type")
    if node_type == "text":
        return str(node.get("text", ""))

    children = node.get("content") or []
    parts = [_adf_to_text(child) for child in children]

    if node_type in ("paragraph", "heading", "listItem"):
        return "".join(parts) + "\n"
    if node_type in ("bulletList", "orderedList"):
        return "".join(f"- {p}" for p in parts)
    return "".join(parts)


def _name_of(value: Any) -> str:
    """Both REST and MCP responses represent status/issuetype/priority as
    either `{"name": "..."}` or a bare string, depending on server/field —
    normalize either shape to a plain string."""
    if isinstance(value, dict):
        return str(value.get("name", ""))
    return str(value or "")


class JiraApiError(Exception):
    """Raised internally when Jira's API returns an error response."""


class JiraTool:
    """Fetches issue summary/description/status from Jira."""

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

    def __init__(self, config: dict[str, Any]) -> None:
        # REST transport config
        self._base_url: str = config.get("jira_base_url", "").rstrip("/")
        self._token: str = config.get("jira_api_token", "")
        self._email: str = config.get("jira_email", "")

        # MCP transport config. Tool/argument names are configurable, not
        # hardcoded, because they're defined by whichever MCP server is
        # actually deployed — there's no single standard tool schema for
        # "get a Jira issue" across servers yet. Defaults match Atlassian's
        # own remote MCP server's naming as of writing; check your server's
        # tool list (or its docs) and override via config if it differs.
        self._mcp_server_url: str = config.get("jira_mcp_server_url", "").rstrip("/")
        self._mcp_auth_token: str = config.get("jira_mcp_api_key", "")
        self._mcp_tool_name: str = config.get("jira_mcp_tool_name", "getJiraIssue")
        self._mcp_key_arg: str = config.get("jira_mcp_key_arg", "issueIdOrKey")

    @property
    def _uses_mcp(self) -> bool:
        return bool(self._mcp_server_url)

    def requires_auth(self) -> bool:
        return True

    @property
    def _uses_rest(self) -> bool:
        return bool(self._base_url and self._email and self._token)

    async def execute(self, input: ToolInput) -> ToolResult:
        if not self._uses_mcp and not self._uses_rest:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="Jira is not configured (missing base URL, email, or API token; "
                "or an MCP server URL).",
            )

        issue_key = extract_issue_key(input.query)
        if issue_key is None:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="No Jira issue key found in the query.",
            )

        if self._uses_mcp:
            result = await self._execute_via_mcp(issue_key)
            # A known MCP endpoint may be auto-wired from the same
            # credential as REST (see app.tools.setup) rather than actually
            # verified compatible - e.g. Atlassian's hosted server needs
            # OAuth, which _execute_via_mcp's bearer-token auth cannot
            # satisfy. Rather than surface that failure, fall back to REST
            # when it's available so an auto-wired MCP attempt can never
            # regress an otherwise-working connection.
            if result.success or not self._uses_rest:
                return result
            logger.info("jira_tool_mcp_fallback_to_rest key=%s", issue_key)
            return await self._execute_via_rest(issue_key)
        return await self._execute_via_rest(issue_key)

    async def _execute_via_mcp(self, issue_key: str) -> ToolResult:
        try:
            payload = await call_mcp_tool(
                self._mcp_server_url,
                self._mcp_tool_name,
                {self._mcp_key_arg: issue_key},
                auth_token=self._mcp_auth_token or None,
            )
        except MCPToolError as exc:
            logger.warning("jira_tool_mcp_failed key=%s error=%s", issue_key, str(exc))
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=str(exc),
            )

        # Server response shape isn't standardized across MCP servers —
        # this normalizes the field names Atlassian's own remote MCP server
        # uses today, with a couple of common fallbacks. If your server's
        # shape differs, this is the one place to adjust it.
        fields = payload.get("fields", payload)
        summary_text = str(fields.get("summary") or payload.get("summary") or "")
        description_raw = fields.get("description", payload.get("description"))
        description = (
            _adf_to_text(description_raw)
            if isinstance(description_raw, dict)
            else str(description_raw or "")
        ).strip()
        status = _name_of(fields.get("status"))
        issue_type = _name_of(fields.get("issuetype") or fields.get("issueType"))
        priority = _name_of(fields.get("priority"))
        labels: list[str] = fields.get("labels") or []

        return self._build_result(
            issue_key,
            summary_text=summary_text,
            description=description,
            status=status,
            issue_type=issue_type,
            priority=priority,
            labels=labels,
            url=str(payload.get("url") or payload.get("self") or ""),
        )

    async def _execute_via_rest(self, issue_key: str) -> ToolResult:
        try:
            # follow_redirects=True: httpx does NOT follow redirects by
            # default. A 3xx (e.g. http->https, or a trailing-slash/base-URL
            # mismatch bouncing to Atlassian's login page) isn't caught by
            # raise_for_status() (only 4xx/5xx are "errors" to httpx), so
            # without this the code fell through to response.json() on a
            # redirect's near-empty body and raised a bare, unhelpful
            # JSONDecodeError instead of a clear cause.
            async with httpx.AsyncClient(
                auth=(self._email, self._token), timeout=10.0, follow_redirects=True
            ) as client:
                response = await client.get(
                    f"{self._base_url}/rest/api/3/issue/{issue_key}",
                    params={"fields": "summary,description,status,issuetype,priority,labels"},
                    headers={"Accept": "application/json"},
                )
            if response.status_code == 404:
                raise JiraApiError(f"Issue '{issue_key}' not found.")
            if response.status_code == 401:
                raise JiraApiError("Jira authentication failed — check email/API token.")
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type:
                raise JiraApiError(
                    f"Jira returned a non-JSON response (content-type: {content_type or 'none'}, "
                    f"status: {response.status_code}) — check the base URL is correct "
                    f"(e.g. https://yourorg.atlassian.net, no trailing path)."
                )
            payload = response.json()
        except JiraApiError as exc:
            logger.warning("jira_tool_fetch_failed key=%s error=%s", issue_key, str(exc))
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=str(exc),
            )
        except httpx.HTTPError as exc:
            logger.warning("jira_tool_http_error key=%s error=%s", issue_key, str(exc))
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=f"Jira request failed: {exc}",
            )

        fields = payload.get("fields", {})
        summary_text: str = fields.get("summary", "")
        description = _adf_to_text(fields.get("description")).strip()
        status = (fields.get("status") or {}).get("name", "")
        issue_type = (fields.get("issuetype") or {}).get("name", "")
        priority = (fields.get("priority") or {}).get("name", "")
        labels: list[str] = fields.get("labels") or []

        return self._build_result(
            issue_key,
            summary_text=summary_text,
            description=description,
            status=status,
            issue_type=issue_type,
            priority=priority,
            labels=labels,
            url=f"{self._base_url}/browse/{issue_key}",
        )

    async def search_issues(self, query: str, max_results: int = 15) -> list[dict[str, str]]:
        """Free-text JQL search — the real "browse the backlog" entry point,
        as opposed to `execute()`'s "fetch one issue by key" (which requires
        already knowing the exact key, and used to be the *only* way a user
        could reference a Jira ticket: paste a key/URL into the objective
        textarea and hope `extract_issue_key` found it). JQL search itself
        is REST only — no standardized MCP search tool exists across
        servers the way "get one issue by key" does.

        When `query` already looks like a bare issue key (e.g. "NPT-6") and
        JQL search isn't available (MCP-only connection, or REST search
        failed), falls back to a direct single-issue fetch via `execute()`
        — that path works over both REST and MCP, so a user who already
        knows the key still gets a real, selectable result instead of a
        blanket "no matches" purely because the *search* endpoint happens
        to be REST-only. Free-text search on an MCP-only connection still
        can't return anything — there is no MCP search tool to fall back to
        for that case.

        Returns [] (not an error) when Jira isn't configured or the search
        itself fails — this is a "nice to have while typing" affordance,
        not a required step, so callers show an empty result list rather
        than surfacing a hard error for what's still an optional picker.
        """
        if not query.strip():
            return []

        if not self._uses_rest:
            return await self._search_fallback_single_key(query)
        # `query` is free text from an authenticated user (GET /jira/search?q=...,
        # only length-bounded, no character filtering) embedded directly into a
        # JQL string literal below — a `"` in the input would otherwise close
        # the literal early and let arbitrary JQL clauses be appended
        # (bounded by whatever the shared Jira credential can see, but a real
        # injection into a query language regardless). JQL's own escaping
        # rules for string literals are backslash and double-quote, same as
        # most C-like query languages.
        escaped_query = query.strip().replace("\\", "\\\\").replace('"', '\\"')
        jql = f'text ~ "{escaped_query}*" ORDER BY updated DESC'
        try:
            async with httpx.AsyncClient(
                auth=(self._email, self._token), timeout=10.0, follow_redirects=True
            ) as client:
                response = await client.get(
                    f"{self._base_url}/rest/api/3/search",
                    params={
                        "jql": jql,
                        "maxResults": max_results,
                        "fields": "summary,status,issuetype",
                    },
                    headers={"Accept": "application/json"},
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("jira_tool_search_failed query=%s error=%s", query, str(exc))
            return await self._search_fallback_single_key(query)

        results: list[dict[str, str]] = []
        for issue in payload.get("issues", []):
            fields = issue.get("fields", {})
            results.append(
                {
                    "key": issue.get("key", ""),
                    "summary": fields.get("summary", ""),
                    "status": _name_of(fields.get("status")),
                    "issue_type": _name_of(fields.get("issuetype")),
                    "url": f"{self._base_url}/browse/{issue.get('key', '')}",
                }
            )

        if not results:
            return await self._search_fallback_single_key(query)
        return results

    async def _search_fallback_single_key(self, query: str) -> list[dict[str, str]]:
        """If `query` names a real issue key, fetch that one issue via the
        existing REST-or-MCP `execute()` path and return it as a
        single-item search result. Not a real search (nothing else in the
        project is discoverable this way), but it means a user who already
        knows the key gets a real, clickable result on an MCP-only
        connection instead of an unconditional "no matches"."""
        issue_key = extract_issue_key(query)
        if issue_key is None:
            return []
        result = await self.execute(ToolInput(query=issue_key))
        if not result.success:
            return []
        return [
            {
                "key": result.data.get("issue_key", issue_key),
                "summary": result.data.get("summary", ""),
                "status": result.data.get("status", ""),
                "issue_type": result.data.get("issue_type", ""),
                "url": result.data.get("url", ""),
            }
        ]

    def _build_result(
        self,
        issue_key: str,
        *,
        summary_text: str,
        description: str,
        status: str,
        issue_type: str,
        priority: str,
        labels: list[str],
        url: str,
    ) -> ToolResult:
        """Shared result shape for both transports — same ToolResult either
        way, so nothing downstream (Planning Agent, evidence, the prompt
        context builder) needs to know or care which one ran."""
        context_text = (
            f"Jira {issue_type or 'ticket'} {issue_key} — {summary_text}\n"
            f"Status: {status}"
            + (f" | Priority: {priority}" if priority else "")
            + "\n"
            + (f"Labels: {', '.join(labels)}\n" if labels else "")
            + (f"\nDescription:\n{description}" if description else "")
        )

        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={
                "issue_key": issue_key,
                "summary": summary_text,
                "description": description,
                "status": status,
                "issue_type": issue_type,
                "priority": priority,
                "labels": labels,
                "context_text": context_text,
                "url": url,
            },
            summary=f"Fetched Jira issue {issue_key}: {summary_text}",
            evidence_items=[context_text],
        )

    async def health_check(self) -> ToolHealth:
        if self._uses_mcp:
            health = await self._health_check_mcp()
            # Same auto-wired-MCP-may-not-actually-work concern as execute():
            # don't report the tool unhealthy over a guessed MCP endpoint
            # when the REST credentials it was reused from are fine.
            if health in (ToolHealth.HEALTHY, ToolHealth.RATE_LIMITED) or not self._uses_rest:
                return health
            return await self._health_check_rest()
        return await self._health_check_rest()

    async def _health_check_mcp(self) -> ToolHealth:
        try:
            await call_mcp_tool(
                self._mcp_server_url,
                self._mcp_tool_name,
                {self._mcp_key_arg: "PING-0"},
                auth_token=self._mcp_auth_token or None,
                timeout=5.0,
            )
            return ToolHealth.HEALTHY
        except MCPToolError as exc:
            # A "not found"-shaped error for a nonsense key still proves
            # the server is reachable and the tool call itself works —
            # only a connection/auth failure should read as unhealthy.
            text = str(exc).lower()
            if "not found" in text or "does not exist" in text or "no issue" in text:
                return ToolHealth.HEALTHY
            if "auth" in text or "401" in text or "403" in text or "permission" in text:
                return ToolHealth.AUTH_FAILED
            return ToolHealth.OFFLINE

    async def _health_check_rest(self) -> ToolHealth:
        if not self._base_url or not self._token or not self._email:
            return ToolHealth.UNCONFIGURED
        try:
            async with httpx.AsyncClient(
                auth=(self._email, self._token), timeout=5.0, follow_redirects=True
            ) as client:
                response = await client.get(f"{self._base_url}/rest/api/3/myself")
            if response.status_code == 401:
                return ToolHealth.AUTH_FAILED
            if response.status_code >= 500:
                return ToolHealth.UNAVAILABLE
            if response.status_code >= 400:
                return ToolHealth.OFFLINE
            # A 2xx status alone doesn't prove this is a Jira API host: a
            # wrong base URL (e.g. Atlassian's *account* home page rather
            # than the Jira Cloud site itself) can 200 with an HTML SPA
            # shell for any path, credentials notwithstanding. Only a real
            # JSON payload counts as healthy.
            if "application/json" not in response.headers.get("content-type", ""):
                return ToolHealth.OFFLINE
            return ToolHealth.HEALTHY
        except httpx.HTTPError:
            return ToolHealth.OFFLINE
