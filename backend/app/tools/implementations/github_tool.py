"""GitHub Tool — real implementation, REST or MCP.

Fetches a single pull request or issue's title/body/state given a full URL
("https://github.com/org/repo/pull/42", "…/issues/7") or GitHub's
"owner/repo#42" shorthand found anywhere in the tool's `query` string. Used
by the Planning Agent so a goal that references a GitHub PR/issue plans
against its real, current content instead of the literal URL string — the
same enrichment Jira gets (see app.tools.implementations.jira_tool).

Two transports, one tool: REST (a personal-access/OAuth token, against
`GET /repos/{owner}/{repo}/issues/{number}` — GitHub itself serves plain
issues and pull requests from that one endpoint, only pull requests carry a
`pull_request` key) if `github_token` is set, or MCP (GitHub's own hosted
MCP server, which accepts that same token as a bearer credential) if
`github_mcp_server_url` is set. MCP is tried first when both are present
and falls back to REST on any failure — the auto-wired endpoint may not
actually be reachable/compatible, so a working REST path must never
regress (see JiraTool's docstring for the identical reasoning).

Unlike Jira/Confluence, GitHub access here is per-user (an OAuth
connection, not an install-wide credential — see
app.models.github_connection), so this tool is never registered with the
global Tool Registry singleton. Callers construct one instance per run
using that run's own user's token — see PlanningAgent's GitHub enrichment
block, which mirrors its Jira enrichment block.
"""

from __future__ import annotations

import logging
import re

import httpx

from app.tools.interfaces import (
    ToolCategory,
    ToolHealth,
    ToolInput,
    ToolResult,
)
from app.tools.mcp_support import MCPToolError, call_mcp_tool

logger = logging.getLogger(__name__)

# "owner/repo#42" or a github.com/.../pull|issues/42 URL — captures
# (owner, repo, kind, number) so REST/MCP can both use the right endpoint.
_URL_PATTERN = re.compile(
    r"github\.com/([\w.-]+)/([\w.-]+)/(pull|issues)/(\d+)"
)
_SHORTHAND_PATTERN = re.compile(r"\b([\w.-]+)/([\w.-]+)#(\d+)\b")


def extract_pr_or_issue_ref(text: str) -> tuple[str, str, int] | None:
    """Return (owner, repo, number) for the first PR/issue reference found,
    or None. The "kind" (pull vs issues) from a URL match doesn't change
    which endpoint we call — GitHub serves both from /issues/{number} — so
    it's discarded rather than threaded through."""
    match = _URL_PATTERN.search(text)
    if match:
        owner, repo, _kind, number = match.groups()
        return owner, repo, int(number)
    match = _SHORTHAND_PATTERN.search(text)
    if match:
        owner, repo, number = match.groups()
        return owner, repo, int(number)
    return None


class GitHubApiError(Exception):
    """Raised internally when GitHub's API returns an error response."""


class GitHubTool:
    """Fetches PR/issue metadata from GitHub."""

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
        # REST transport config
        self._token: str = config.get("github_token", "")
        self._api_url: str = config.get("github_api_url", "https://api.github.com").rstrip("/")

        # MCP transport config. Tool/argument names are configurable, not
        # hardcoded, for the same reason as JiraTool's: no single standard
        # tool schema is guaranteed across servers. Defaults match GitHub's
        # own hosted MCP server's naming as of writing.
        self._mcp_server_url: str = config.get("github_mcp_server_url", "").rstrip("/")
        self._mcp_auth_token: str = config.get("github_mcp_api_key", "") or self._token
        self._mcp_pr_tool_name: str = config.get("github_mcp_pr_tool_name", "get_pull_request")
        self._mcp_issue_tool_name: str = config.get("github_mcp_issue_tool_name", "get_issue")

    @property
    def _uses_mcp(self) -> bool:
        return bool(self._mcp_server_url)

    @property
    def _uses_rest(self) -> bool:
        return bool(self._token)

    def requires_auth(self) -> bool:
        return True

    async def execute(self, input: ToolInput) -> ToolResult:
        if not self._uses_mcp and not self._uses_rest:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="GitHub is not configured (missing a token, or an MCP server URL).",
            )

        ref = extract_pr_or_issue_ref(input.query)
        if ref is None:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="No GitHub PR/issue reference found in the query.",
            )
        owner, repo, number = ref

        if self._uses_mcp:
            result = await self._execute_via_mcp(owner, repo, number)
            # Same reasoning as JiraTool: an auto-wired MCP endpoint may not
            # actually be reachable/compatible - never let that regress a
            # working REST connection.
            if result.success or not self._uses_rest:
                return result
            logger.info("github_tool_mcp_fallback_to_rest owner=%s repo=%s number=%s", owner, repo, number)
            return await self._execute_via_rest(owner, repo, number)
        return await self._execute_via_rest(owner, repo, number)

    async def _execute_via_rest(self, owner: str, repo: str, number: int) -> ToolResult:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(
                    f"{self._api_url}/repos/{owner}/{repo}/issues/{number}",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {self._token}",
                    },
                )
            if response.status_code == 404:
                raise GitHubApiError(f"'{owner}/{repo}#{number}' not found (or not accessible).")
            if response.status_code == 401:
                raise GitHubApiError("GitHub authentication failed — check the token.")
            response.raise_for_status()
            payload = response.json()
        except GitHubApiError as exc:
            logger.warning("github_tool_fetch_failed ref=%s/%s#%s error=%s", owner, repo, number, str(exc))
            return ToolResult(
                tool_id=self.tool_id, tool_name=self.display_name, success=False, error=str(exc),
            )
        except httpx.HTTPError as exc:
            logger.warning("github_tool_http_error ref=%s/%s#%s error=%s", owner, repo, number, str(exc))
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=f"GitHub request failed: {exc}",
            )

        is_pr = "pull_request" in payload
        return self._build_result(
            owner, repo, number,
            title=str(payload.get("title") or ""),
            body=str(payload.get("body") or ""),
            state=str(payload.get("state") or ""),
            is_pr=is_pr,
            labels=[label.get("name", "") for label in payload.get("labels") or []],
            url=str(payload.get("html_url") or ""),
        )

    async def _execute_via_mcp(self, owner: str, repo: str, number: int) -> ToolResult:
        # We don't know PR-vs-issue until GitHub answers (see REST path's
        # `is_pr`), but MCP tools are usually split by kind — try the PR
        # tool first, since a PR number and an issue number share the same
        # counter and "not found" from the wrong tool is unambiguous.
        for tool_name, arg_name, is_pr in (
            (self._mcp_pr_tool_name, "pullNumber", True),
            (self._mcp_issue_tool_name, "issueNumber", False),
        ):
            try:
                payload = await call_mcp_tool(
                    self._mcp_server_url,
                    tool_name,
                    {"owner": owner, "repo": repo, arg_name: number},
                    auth_token=self._mcp_auth_token or None,
                )
            except MCPToolError as exc:
                text = str(exc).lower()
                if is_pr and ("not found" in text or "no pull request" in text):
                    continue  # fall through to the issue tool
                logger.warning("github_tool_mcp_failed ref=%s/%s#%s error=%s", owner, repo, number, str(exc))
                return ToolResult(
                    tool_id=self.tool_id, tool_name=self.display_name, success=False, error=str(exc),
                )

            return self._build_result(
                owner, repo, number,
                title=str(payload.get("title") or ""),
                body=str(payload.get("body") or payload.get("description") or ""),
                state=str(payload.get("state") or ""),
                is_pr=is_pr,
                labels=payload.get("labels") or [],
                url=str(payload.get("url") or payload.get("html_url") or ""),
            )

        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=False,
            error=f"'{owner}/{repo}#{number}' not found as a PR or an issue.",
        )

    def _build_result(
        self, owner: str, repo: str, number: int, *,
        title: str, body: str, state: str, is_pr: bool, labels: list[str], url: str,
    ) -> ToolResult:
        kind = "Pull Request" if is_pr else "Issue"
        context_text = (
            f"GitHub {kind} {owner}/{repo}#{number} — {title}\n"
            f"State: {state}" + (f" | Labels: {', '.join(labels)}" if labels else "") + "\n"
            + (f"\n{body}" if body else "")
        )
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={
                "owner": owner,
                "repo": repo,
                "number": number,
                "title": title,
                "body": body,
                "state": state,
                "is_pull_request": is_pr,
                "labels": labels,
                "context_text": context_text,
                "url": url,
            },
            summary=f"Fetched GitHub {kind.lower()} {owner}/{repo}#{number}: {title}",
            evidence_items=[context_text],
        )

    async def health_check(self) -> ToolHealth:
        if not self._uses_mcp and not self._uses_rest:
            return ToolHealth.UNCONFIGURED
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self._api_url}/rate_limit",
                    headers={"Authorization": f"Bearer {self._token}"} if self._token else {},
                )
            if response.status_code == 401:
                return ToolHealth.AUTH_FAILED
            if response.status_code >= 500:
                return ToolHealth.UNAVAILABLE
            return ToolHealth.HEALTHY if response.status_code < 400 else ToolHealth.OFFLINE
        except httpx.HTTPError:
            return ToolHealth.OFFLINE
