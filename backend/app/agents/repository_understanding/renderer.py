"""Renders a `RepositoryProfile` plus the LLM narrative into the
Repository Understanding Agent's result dict. Pure formatting — no
retrieval, no business logic; every value here was already computed by
`RepositoryProfileService` or the LLM narrative before this module is
called (see `BaseFrontierAgent.run`: `render_response` runs after both).

`BaseFrontierAgent.run` folds this dict into `AgentOutput.result` via
`ResultMapper.to_agent_output` (confidence/evidence/metrics), so this
module never touches `Confidence`/`Evidence` itself.
"""

from __future__ import annotations

from typing import Any

from app.agents.frontier.response_renderer import to_executive_summary, to_markdown
from app.services.engineering_intelligence.contracts import RepositoryProfile


def _str_field(narrative: dict[str, Any], key: str, fallback: str) -> str:
    value = narrative.get(key)
    return value if isinstance(value, str) and value.strip() else fallback


def _list_field(narrative: dict[str, Any], key: str) -> list[str]:
    value = narrative.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def render_repository_understanding(
    profile: RepositoryProfile, narrative: dict[str, Any]
) -> dict[str, Any]:
    executive_summary = _str_field(
        narrative,
        "executive_summary",
        f"{len(profile.apis)} API(s), {len(profile.databases)} database(s), "
        f"{len(profile.queues)} queue(s), {len(profile.integrations)} integration(s).",
    )
    purpose = _str_field(narrative, "purpose", "")
    architecture = _str_field(narrative, "architecture", "")
    apis_summary = _str_field(narrative, "apis", "")
    data_stores_summary = _str_field(narrative, "data_stores", "")
    messaging_summary = _str_field(narrative, "messaging", "")
    external_integrations_summary = _str_field(narrative, "external_integrations", "")
    dependencies_summary = _str_field(narrative, "dependencies", "")
    interesting_findings = _list_field(narrative, "interesting_findings")

    sections: dict[str, str | list[str]] = {
        "Executive Summary": executive_summary,
        "Repository Overview": purpose,
        "Architecture Overview": architecture,
        "API Summary": apis_summary,
        "Database Summary": data_stores_summary,
        "Messaging Summary": messaging_summary,
        "External Systems": external_integrations_summary,
        "Dependency Summary": dependencies_summary,
        "Interesting Findings": interesting_findings,
    }

    return {
        "repository_id": profile.repository_id,
        "executive_summary": to_executive_summary(executive_summary, interesting_findings),
        "repository_overview": purpose,
        "architecture_overview": architecture,
        "api_summary": apis_summary,
        "apis": list(profile.apis),
        "database_summary": data_stores_summary,
        "databases": list(profile.databases),
        "messaging_summary": messaging_summary,
        "queues": list(profile.queues),
        "external_systems_summary": external_integrations_summary,
        "integrations": list(profile.integrations),
        "dependency_summary": dependencies_summary,
        "dependencies": list(profile.dependencies),
        "interesting_findings": interesting_findings,
        "markdown": to_markdown("Repository Understanding Report", sections),
    }
