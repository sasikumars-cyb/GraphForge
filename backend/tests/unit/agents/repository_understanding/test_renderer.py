"""`render_repository_understanding` — pure formatting, no I/O."""

from __future__ import annotations

from app.agents.repository_understanding.renderer import render_repository_understanding
from app.services.engineering_intelligence.contracts import RepositoryProfile


def _profile() -> RepositoryProfile:
    return RepositoryProfile(
        repository_id="repo-1",
        apis=("GET /orders",),
        databases=("orders",),
        queues=("order-events",),
        integrations=("BillingClient",),
        dependencies=("org.apache:commons-lang3",),
    )


def test_render_uses_narrative_fields_when_present() -> None:
    narrative = {
        "executive_summary": "A checkout service.",
        "purpose": "Places and tracks orders.",
        "architecture": "Data-owning service.",
        "apis": "One order-placement endpoint.",
        "data_stores": "Owns the orders table.",
        "messaging": "Publishes order events.",
        "external_integrations": "Calls billing.",
        "dependencies": "Depends on Apache Commons.",
        "interesting_findings": ["Only one API despite owning a database."],
    }

    rendered = render_repository_understanding(_profile(), narrative)

    assert rendered["repository_overview"] == "Places and tracks orders."
    assert rendered["architecture_overview"] == "Data-owning service."
    assert rendered["api_summary"] == "One order-placement endpoint."
    assert rendered["database_summary"] == "Owns the orders table."
    assert rendered["messaging_summary"] == "Publishes order events."
    assert rendered["external_systems_summary"] == "Calls billing."
    assert rendered["dependency_summary"] == "Depends on Apache Commons."
    assert rendered["interesting_findings"] == ["Only one API despite owning a database."]
    assert rendered["apis"] == ["GET /orders"]
    assert "# Repository Understanding Report" in rendered["markdown"]
    assert "A checkout service." in rendered["executive_summary"]


def test_render_falls_back_to_computed_summary_when_narrative_is_empty() -> None:
    rendered = render_repository_understanding(_profile(), {})

    assert "1 API(s)" in rendered["executive_summary"]
    assert rendered["repository_overview"] == ""
    assert rendered["apis"] == ["GET /orders"]


def test_render_ignores_non_string_and_non_list_narrative_pollution() -> None:
    narrative = {"executive_summary": 42, "interesting_findings": "not a list", "purpose": None}

    rendered = render_repository_understanding(_profile(), narrative)

    assert "1 API(s)" in rendered["executive_summary"]
    assert rendered["interesting_findings"] == []
    assert rendered["repository_overview"] == ""
