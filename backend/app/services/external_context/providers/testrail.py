from __future__ import annotations

import re
from typing import Any

import httpx

from app.services.external_context.base import ExternalContextItem, ExternalContextProvider


class TestRailProvider(ExternalContextProvider):
    name = "testrail"

    async def collect(self, user_input: str, config: dict[str, Any]) -> list[ExternalContextItem]:
        matches = re.findall(r"\b(?:suite|testrail)\s+#?(\d+)\b", user_input, flags=re.IGNORECASE)
        if not matches:
            return []
        base_url = (config.get("base_url") or "").rstrip("/")
        api_key = config.get("api_key", "")
        project_id = config.get("project_id")
        section_id = config.get("section_id")
        if not base_url or not api_key:
            return []

        items: list[ExternalContextItem] = []
        for suite_id in matches:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(
                        f"{base_url}/index.php?/api/v2/get_suite/{suite_id}",
                        auth=("", api_key),
                    )
                    response.raise_for_status()
                    payload = response.json()
            except Exception:
                continue
            items.append(
                ExternalContextItem(
                    provider="testrail",
                    title=payload.get("name", f"TestRail suite {suite_id}"),
                    summary=payload.get("description", ""),
                    details=f"project_id={project_id}; section_id={section_id}",
                    references=[f"suite:{suite_id}"],
                    raw={"suite_id": suite_id},
                )
            )
        return items
