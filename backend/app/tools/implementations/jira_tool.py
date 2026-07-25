"""Jira Tool — real implementation.

Fetches a single issue's summary/description/status from Jira Cloud's REST
API v3 (Basic Auth: email + API token) given either a bare issue key
("NPT-6") or a full ticket URL
("https://myorg.atlassian.net/browse/NPT-6") anywhere in the tool's
`query` string. Used by the Planning Agent so a goal that references a
Jira ticket plans against the ticket's real, current content instead of
the literal URL string.
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

    def __init__(self, config: dict) -> None:
        self._base_url: str = config.get("jira_base_url", "").rstrip("/")
        self._token: str = config.get("jira_api_token", "")
        self._email: str = config.get("jira_email", "")

    def requires_auth(self) -> bool:
        return True

    async def execute(self, input: ToolInput) -> ToolResult:
        if not self._base_url or not self._email or not self._token:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="Jira is not configured (missing base URL, email, or API token).",
            )

        issue_key = extract_issue_key(input.query)
        if issue_key is None:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="No Jira issue key found in the query.",
            )

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

        context_text = (
            f"Jira {issue_type or 'ticket'} {issue_key} — {summary_text}\n"
            f"Status: {status}" + (f" | Priority: {priority}" if priority else "") + "\n"
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
                "url": f"{self._base_url}/browse/{issue_key}",
            },
            summary=f"Fetched Jira issue {issue_key}: {summary_text}",
            evidence_items=[context_text],
        )

    async def health_check(self) -> ToolHealth:
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
            return ToolHealth.HEALTHY if response.status_code < 400 else ToolHealth.OFFLINE
        except httpx.HTTPError:
            return ToolHealth.OFFLINE
