"""GitHub Tool — real implementation, REST or MCP.

Fetches a single pull request or issue's title/body/state given a full URL
("https://github.com/org/repo/pull/42", "…/issues/7") or GitHub's
"owner/repo#42" shorthand found anywhere in the tool's `query` string. Used
by the Planning Agent so a goal that references a GitHub PR/issue plans
against its real, current content instead of the literal URL string — the
same enrichment Jira gets (see app.tools.implementations.jira_tool).

Also exposes `get_repository`/`get_file_contents` — repository-overview and
raw-file lookups, called directly (not through `execute()`'s regex-based
PR/issue detection) by callers that already know what they're asking for,
e.g. `context_pipeline.providers.GitHubProvider` resolving a
`GITHUB_REPOSITORY` reference (a bare "owner/repo" mention with no
`#issue`/`#pr` — `execute()`'s PR/issue regex never matches that shape, so
without these methods that reference type was detected but never actually
fetched).

Two transports, one tool: REST (a personal-access/OAuth token, against
`GET /repos/{owner}/{repo}/issues/{number}` — GitHub itself serves plain
issues and pull requests from that one endpoint, only pull requests carry a
`pull_request` key) if `github_token` is set, or MCP (GitHub's own hosted
MCP server, which accepts that same token as a bearer credential) if
`github_mcp_server_url` is set. MCP is tried first when both are present
and falls back to REST on any failure — the auto-wired endpoint may not
actually be reachable/compatible, so a working REST path must never
regress (see JiraTool's docstring for the identical reasoning). Every
lookup this class exposes (PR/issue, repository, file contents) follows
that same try-MCP-then-REST shape.

Unlike Jira/Confluence, GitHub access here is per-user (an OAuth
connection, not an install-wide credential — see
app.models.github_connection), so this tool is never registered with the
global Tool Registry singleton. Callers construct one instance per run
using that run's own user's token — see PlanningAgent's GitHub enrichment
block, which mirrors its Jira enrichment block.
"""

from __future__ import annotations

import base64
import contextlib
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

# "owner/repo#42" or a github.com/.../pull|issues/42 URL — captures
# (owner, repo, kind, number) so REST/MCP can both use the right endpoint.
_URL_PATTERN = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/(pull|issues)/(\d+)")
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
    """Fetches PR/issue metadata, repository overviews, and file contents
    from GitHub."""

    tool_id = "github"
    display_name = "GitHub"
    description = (
        "Fetches pull request metadata, open issues, repository overviews, and "
        "file contents from the GitHub API. Used by the Planning Agent and the "
        "Context Discovery reasoning engine to ground plans in real, current "
        "repository state instead of a referenced URL/name alone."
    )
    category = ToolCategory.CODE_INTELLIGENCE
    capabilities = [
        "pull_requests",
        "issues",
        "repository_activity",
        "code_reviews",
        "repository_metadata",
        "file_contents",
    ]

    def __init__(self, config: dict[str, Any]) -> None:
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
        self._mcp_repo_tool_name: str = config.get("github_mcp_repo_tool_name", "get_repository")
        self._mcp_file_tool_name: str = config.get("github_mcp_file_tool_name", "get_file_contents")

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
            logger.info(
                "github_tool_mcp_fallback_to_rest owner=%s repo=%s number=%s", owner, repo, number
            )
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
            logger.warning(
                "github_tool_fetch_failed ref=%s/%s#%s error=%s", owner, repo, number, str(exc)
            )
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=str(exc),
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "github_tool_http_error ref=%s/%s#%s error=%s", owner, repo, number, str(exc)
            )
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=f"GitHub request failed: {exc}",
            )

        is_pr = "pull_request" in payload
        return self._build_result(
            owner,
            repo,
            number,
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
                logger.warning(
                    "github_tool_mcp_failed ref=%s/%s#%s error=%s", owner, repo, number, str(exc)
                )
                return ToolResult(
                    tool_id=self.tool_id,
                    tool_name=self.display_name,
                    success=False,
                    error=str(exc),
                )

            return self._build_result(
                owner,
                repo,
                number,
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
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        title: str,
        body: str,
        state: str,
        is_pr: bool,
        labels: list[str],
        url: str,
    ) -> ToolResult:
        kind = "Pull Request" if is_pr else "Issue"
        context_text = f"GitHub {kind} {owner}/{repo}#{number} — {title}\n" f"State: {state}" + (
            f" | Labels: {', '.join(labels)}" if labels else ""
        ) + "\n" + (f"\n{body}" if body else "")
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

    # ------------------------------------------------------------------
    # Repository overview — called directly by callers that already have
    # an (owner, repo) pair, not via execute()'s regex-based query parsing.
    # ------------------------------------------------------------------

    async def get_repository(self, owner: str, repo: str) -> ToolResult:
        if not self._uses_mcp and not self._uses_rest:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="GitHub is not configured (missing a token, or an MCP server URL).",
            )

        if self._uses_mcp:
            result = await self._get_repository_via_mcp(owner, repo)
            if result.success or not self._uses_rest:
                return result
            logger.info("github_tool_repo_mcp_fallback_to_rest owner=%s repo=%s", owner, repo)
            return await self._get_repository_via_rest(owner, repo)
        return await self._get_repository_via_rest(owner, repo)

    async def _get_repository_via_rest(self, owner: str, repo: str) -> ToolResult:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(
                    f"{self._api_url}/repos/{owner}/{repo}",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {self._token}",
                    },
                )
            if response.status_code == 404:
                raise GitHubApiError(f"Repository '{owner}/{repo}' not found (or not accessible).")
            if response.status_code == 401:
                raise GitHubApiError("GitHub authentication failed — check the token.")
            response.raise_for_status()
            payload = response.json()
        except GitHubApiError as exc:
            logger.warning(
                "github_tool_repo_fetch_failed ref=%s/%s error=%s", owner, repo, str(exc)
            )
            return ToolResult(
                tool_id=self.tool_id, tool_name=self.display_name, success=False, error=str(exc)
            )
        except httpx.HTTPError as exc:
            logger.warning("github_tool_repo_http_error ref=%s/%s error=%s", owner, repo, str(exc))
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=f"GitHub request failed: {exc}",
            )

        return self._build_repository_result(
            owner,
            repo,
            description=str(payload.get("description") or ""),
            default_branch=str(payload.get("default_branch") or ""),
            language=str(payload.get("language") or ""),
            topics=list(payload.get("topics") or []),
            stars=int(payload.get("stargazers_count") or 0),
            url=str(payload.get("html_url") or ""),
        )

    async def _get_repository_via_mcp(self, owner: str, repo: str) -> ToolResult:
        try:
            payload = await call_mcp_tool(
                self._mcp_server_url,
                self._mcp_repo_tool_name,
                {"owner": owner, "repo": repo},
                auth_token=self._mcp_auth_token or None,
            )
        except MCPToolError as exc:
            logger.warning("github_tool_repo_mcp_failed ref=%s/%s error=%s", owner, repo, str(exc))
            return ToolResult(
                tool_id=self.tool_id, tool_name=self.display_name, success=False, error=str(exc)
            )

        return self._build_repository_result(
            owner,
            repo,
            description=str(payload.get("description") or ""),
            default_branch=str(payload.get("default_branch") or payload.get("defaultBranch") or ""),
            language=str(payload.get("language") or ""),
            topics=list(payload.get("topics") or []),
            stars=int(payload.get("stargazers_count") or payload.get("stars") or 0),
            url=str(payload.get("url") or payload.get("html_url") or ""),
        )

    def _build_repository_result(
        self,
        owner: str,
        repo: str,
        *,
        description: str,
        default_branch: str,
        language: str,
        topics: list[str],
        stars: int,
        url: str,
    ) -> ToolResult:
        context_text = (
            f"GitHub repository {owner}/{repo}"
            + (f" — {description}" if description else "")
            + (f"\nPrimary language: {language}" if language else "")
            + (f" | Default branch: {default_branch}" if default_branch else "")
            + (f"\nTopics: {', '.join(topics)}" if topics else "")
        )
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={
                "owner": owner,
                "repo": repo,
                "description": description,
                "default_branch": default_branch,
                "language": language,
                "topics": topics,
                "stars": stars,
                "context_text": context_text,
                "url": url,
            },
            summary=f"Fetched GitHub repository {owner}/{repo}"
            + (f": {description}" if description else ""),
            evidence_items=[context_text],
        )

    # ------------------------------------------------------------------
    # File contents — same direct-call shape as get_repository above.
    # ------------------------------------------------------------------

    async def get_file_contents(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> ToolResult:
        if not self._uses_mcp and not self._uses_rest:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="GitHub is not configured (missing a token, or an MCP server URL).",
            )

        if self._uses_mcp:
            result = await self._get_file_contents_via_mcp(owner, repo, path, ref)
            if result.success or not self._uses_rest:
                return result
            logger.info(
                "github_tool_file_mcp_fallback_to_rest owner=%s repo=%s path=%s", owner, repo, path
            )
            return await self._get_file_contents_via_rest(owner, repo, path, ref)
        return await self._get_file_contents_via_rest(owner, repo, path, ref)

    async def _get_file_contents_via_rest(
        self, owner: str, repo: str, path: str, ref: str | None
    ) -> ToolResult:
        # `.raw+json` — same trick GitHubVersionControlProvider.get_file_content
        # uses (app.integrations.github): the body is the file itself, not a
        # base64-wrapped JSON envelope. Duplicated rather than reused because
        # that class talks to api.github.com directly with no MCP fallback and
        # no configurable base URL — this tool owns its own transport choice.
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(
                    f"{self._api_url}/repos/{owner}/{repo}/contents/{path}",
                    headers={
                        "Accept": "application/vnd.github.raw+json",
                        "Authorization": f"Bearer {self._token}",
                    },
                    params={"ref": ref} if ref else None,
                )
            if response.status_code == 404:
                raise GitHubApiError(f"'{path}' not found in {owner}/{repo}.")
            if response.status_code == 401:
                raise GitHubApiError("GitHub authentication failed — check the token.")
            response.raise_for_status()
            content = response.text
        except GitHubApiError as exc:
            logger.warning(
                "github_tool_file_fetch_failed ref=%s/%s path=%s error=%s",
                owner,
                repo,
                path,
                str(exc),
            )
            return ToolResult(
                tool_id=self.tool_id, tool_name=self.display_name, success=False, error=str(exc)
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "github_tool_file_http_error ref=%s/%s path=%s error=%s",
                owner,
                repo,
                path,
                str(exc),
            )
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=f"GitHub request failed: {exc}",
            )

        return self._build_file_result(owner, repo, path, content=content)

    async def _get_file_contents_via_mcp(
        self, owner: str, repo: str, path: str, ref: str | None
    ) -> ToolResult:
        arguments: dict[str, Any] = {"owner": owner, "repo": repo, "path": path}
        if ref:
            arguments["ref"] = ref
        try:
            payload = await call_mcp_tool(
                self._mcp_server_url,
                self._mcp_file_tool_name,
                arguments,
                auth_token=self._mcp_auth_token or None,
            )
        except MCPToolError as exc:
            logger.warning(
                "github_tool_file_mcp_failed ref=%s/%s path=%s error=%s",
                owner,
                repo,
                path,
                str(exc),
            )
            return ToolResult(
                tool_id=self.tool_id, tool_name=self.display_name, success=False, error=str(exc)
            )

        # GitHub's hosted MCP server returns file content either as plain
        # text or base64-encoded (mirroring the REST contents API's own
        # `encoding` field) depending on server version - decode only when
        # told to, never guess from content shape.
        content = str(payload.get("content") or payload.get("text") or "")
        if str(payload.get("encoding") or "").lower() == "base64" and content:
            # Falls through with the raw (still base64) content on a decode
            # failure rather than failing the whole lookup over it.
            with contextlib.suppress(Exception):
                content = base64.b64decode(content).decode("utf-8", errors="replace")

        return self._build_file_result(owner, repo, path, content=content)

    def _build_file_result(self, owner: str, repo: str, path: str, *, content: str) -> ToolResult:
        # Same cap PlanningObservation/Evidence summaries generally apply to
        # tool output reaching an LLM prompt - an arbitrary file could be huge,
        # and this tool has no separate token-budgeting pass of its own.
        _MAX_CHARS = 8000
        truncated = len(content) > _MAX_CHARS
        shown = content[:_MAX_CHARS]
        context_text = f"GitHub file {owner}/{repo}/{path}:\n{shown}" + (
            "\n[...truncated]" if truncated else ""
        )
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={
                "owner": owner,
                "repo": repo,
                "path": path,
                "content": shown,
                "truncated": truncated,
                "context_text": context_text,
            },
            summary=f"Fetched {owner}/{repo}/{path} ({len(content)} chars)",
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
