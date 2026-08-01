"""TestRail Tool — real implementation, REST v2 only.

Lists projects/suites/sections/cases from TestRail for syncing into the
Knowledge Graph (see app.services.testrail_service /
app.indexer.graph.testrail_builder) — unlike JiraTool, this tool's job is a
bulk read for indexing, not a single-reference lookup folded into an LLM
prompt.

Auth: HTTP Basic, with the TestRail account's **email** as username and an
**API key** as password — this is TestRail's actual API contract, not a
bare API key despite how "connect with an API key" is usually phrased. See
app.knowledge.registry's testrail TransportSpec, which already declares
base_url/email/api_token to match.

No MCP transport: no known/standard TestRail MCP server exists, unlike
GitHub/Jira/Confluence.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from app.tools.interfaces import ToolCategory, ToolHealth, ToolResult

logger = logging.getLogger(__name__)

# TestRail's own hard cap for get_cases/get_results-style endpoints.
_PAGE_SIZE = 250
# Safety cap so a single sync can never loop forever against a
# misbehaving server or an unexpectedly enormous project - mirrors the
# bounded-loop convention already used elsewhere in this codebase (e.g.
# RepositoriesPage's BULK_INDEX_POLL_MAX_MS).
_MAX_ITEMS = 20_000


class TestRailApiError(Exception):
    """Raised internally when TestRail's API returns an error response."""


class TestRailTool:
    """Reads projects, suites, sections, and cases from TestRail."""

    tool_id = "testrail"
    display_name = "TestRail"
    description = (
        "Reads test cases, suites, and sections from TestRail so they can be "
        "synced into the Knowledge Graph and grounded against by the Testing agent."
    )
    category = ToolCategory.TESTING
    capabilities = [
        "test_cases",
        "test_suites",
        "test_sections",
        "test_projects",
    ]

    def __init__(self, config: dict[str, Any]) -> None:
        self._base_url: str = config.get("testrail_base_url", "").rstrip("/")
        self._email: str = config.get("testrail_email", "")
        self._token: str = config.get("testrail_api_token", "")

    @property
    def _configured(self) -> bool:
        return bool(self._base_url and self._email and self._token)

    def requires_auth(self) -> bool:
        return True

    async def _get(self, method: str, params: dict[str, Any] | None = None) -> Any:
        # TestRail's API path is embedded literally in the query string
        # ("index.php?/api/v2/get_projects"), not a normal "?key=value"
        # query param. Passing that through httpx's `params=` alongside it
        # does NOT just append "&limit=250" as it looks like it should —
        # httpx parses the URL's existing query string into `QueryParams`
        # first (via `urllib.parse.parse_qsl`, which silently drops any
        # component with no "=" sign), so "/api/v2/get_projects" vanishes
        # entirely and TestRail 404s on the bare "?limit=250&offset=0" that
        # survives. Building the full query string by hand sidesteps that
        # parse/merge step altogether.
        query = f"/api/v2/{method}"
        if params:
            query += "&" + urlencode(params)
        try:
            async with httpx.AsyncClient(
                auth=(self._email, self._token), timeout=15.0, follow_redirects=True
            ) as client:
                response = await client.get(
                    f"{self._base_url}/index.php?{query}",
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise TestRailApiError(f"TestRail request to {method} failed: {exc}") from exc

        if response.status_code == 401:
            raise TestRailApiError("TestRail authentication failed — check email/API key.")
        if response.status_code == 400:
            raise TestRailApiError(f"TestRail rejected the request to {method}: {response.text}")
        if response.is_error:
            raise TestRailApiError(
                f"TestRail request to {method} failed with status {response.status_code}: "
                f"{response.text}"
            )
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            raise TestRailApiError(
                f"TestRail returned a non-JSON response for {method} "
                f"(content-type: {content_type or 'none'}) — check the base URL "
                f"(e.g. https://yourcompany.testrail.io, no trailing path)."
            )
        return response.json()

    async def _get_paginated(self, method: str, list_key: str, params: dict[str, Any]) -> list[Any]:
        """TestRail 6.7+ paginates get_cases/get_sections-style endpoints as
        `{"<list_key>": [...], "_links": {"next": "...offset=N..." | null}}`;
        older instances just return a bare list. Handles both, following
        `_links.next` until exhausted or `_MAX_ITEMS` is reached."""
        items: list[Any] = []
        offset = 0
        while len(items) < _MAX_ITEMS:
            page = await self._get(method, {**params, "limit": _PAGE_SIZE, "offset": offset})
            if isinstance(page, list):
                items.extend(page)
                break  # unpaginated (older TestRail) — one page is everything
            batch = page.get(list_key, [])
            items.extend(batch)
            if not page.get("_links", {}).get("next") or not batch:
                break
            offset += _PAGE_SIZE
        return items[:_MAX_ITEMS]

    async def list_projects(self) -> list[dict[str, Any]]:
        data = await self._get_paginated("get_projects", "projects", {})
        return [
            {"id": p["id"], "name": p["name"], "suite_mode": p.get("suite_mode", 1)} for p in data
        ]

    async def list_suites(self, project_id: int) -> list[dict[str, Any]]:
        data = await self._get(f"get_suites/{project_id}")
        suites = data if isinstance(data, list) else data.get("suites", [])
        return [{"id": s["id"], "name": s["name"]} for s in suites]

    async def list_sections(self, project_id: int, suite_id: int | None) -> list[dict[str, Any]]:
        params = {"suite_id": suite_id} if suite_id is not None else {}
        data = await self._get_paginated(f"get_sections/{project_id}", "sections", params)
        return [
            {
                "id": s["id"],
                "name": s["name"],
                "parent_id": s.get("parent_id"),
                "suite_id": s.get("suite_id"),
            }
            for s in data
        ]

    async def list_cases(self, project_id: int, suite_id: int | None) -> list[dict[str, Any]]:
        params = {"suite_id": suite_id} if suite_id is not None else {}
        data = await self._get_paginated(f"get_cases/{project_id}", "cases", params)
        return [
            {
                "id": c["id"],
                "title": c["title"],
                "section_id": c.get("section_id"),
                "suite_id": c.get("suite_id"),
                "priority_id": c.get("priority_id"),
                "type_id": c.get("type_id"),
                "refs": c.get("refs") or "",
            }
            for c in data
        ]

    async def health_check(self) -> ToolHealth:
        if not self._configured:
            return ToolHealth.UNCONFIGURED
        try:
            await self._get("get_projects", {"limit": 1})
            return ToolHealth.HEALTHY
        except TestRailApiError as exc:
            text = str(exc).lower()
            if "authentication failed" in text:
                return ToolHealth.AUTH_FAILED
            return ToolHealth.OFFLINE
        except Exception:
            logger.warning("testrail_tool_health_check_failed", exc_info=True)
            return ToolHealth.UNAVAILABLE

    # ITool protocol compatibility: TestRailTool is driven directly by
    # app.services.testrail_service (bulk sync), not the generic
    # ToolExecutor.execute()/ToolInput single-query path every other tool
    # uses — this satisfies the ITool shape (tool_id/display_name/
    # description/category/capabilities/execute/health_check/requires_auth)
    # so it still shows up in the Tool Registry UI, but callers doing a
    # sync should call list_projects()/list_cases() etc. directly.
    async def execute(self, input: Any) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=False,
            error=(
                "TestRailTool has no single-query execute() — use list_projects()/"
                "list_cases() directly, or trigger a sync via POST /testrail/projects/{id}/sync."
            ),
        )
