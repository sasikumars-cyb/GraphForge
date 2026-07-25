from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.services.external_context.base import ExternalContextItem, ExternalContextProvider

# Users typically paste the URL straight from their browser, which for Jira
# Cloud includes a web-app path segment (e.g. `/jira`, `/jira/software`)
# that isn't part of the REST API path — the API always lives at
# `<site>.atlassian.net/rest/api/2/...`.
_WEB_APP_SUFFIX_RE = re.compile(r"/jira(?:/(?:software|core|servicedesk))?/?$", re.IGNORECASE)


def _normalize_base_url(base_url: str) -> str:
    stripped = base_url.strip().rstrip("/")
    return _WEB_APP_SUFFIX_RE.sub("", stripped).rstrip("/")


class JiraProvider(ExternalContextProvider):
    name = "jira"

    async def collect(self, user_input: str, config: dict[str, Any]) -> list[ExternalContextItem]:
        issue_keys = re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", user_input)
        if not issue_keys:
            return []
        base_url = _normalize_base_url(config.get("base_url") or "")
        email = config.get("email", "")
        token = config.get("api_token", "")
        project_key = (config.get("project_key") or "").strip()
        if not base_url or not email or not token:
            return []

        items: list[ExternalContextItem] = []
        for issue_key in issue_keys:
            if project_key and project_key.upper() not in issue_key.upper():
                continue
            url = f"{base_url}/rest/api/2/issue/{issue_key}"
            headers = {"Accept": "application/json"}
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    # Jira Cloud REST API auth is HTTP Basic with the
                    # account email + API token — NOT a bearer token.
                    response = await client.get(url, headers=headers, auth=(email, token))
                    response.raise_for_status()
                    payload = response.json()
            except Exception:
                continue
            fields = payload.get("fields", {})
            items.append(
                ExternalContextItem(
                    provider="jira",
                    title=fields.get("summary", issue_key),
                    summary=fields.get("description", ""),
                    details=(fields.get("acceptancecriteria") or ""),
                    references=[issue_key],
                    raw={"issue_key": issue_key, "status": fields.get("status", {}).get("name", "")},
                )
            )
        return items
