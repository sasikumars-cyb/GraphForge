"""`render_impact_analysis` — pure formatting, no I/O."""

from __future__ import annotations

from app.agents.impact_analysis.renderer import render_impact_analysis
from app.services.engineering_intelligence.contracts import (
    BlastRadius,
    EntityReference,
    RelationshipInsight,
)


def _insight(state: str) -> RelationshipInsight:
    return RelationshipInsight(
        relationship_key=f"key-{state}",
        relationship_type="CALLS_SERVICE",
        source_entity="repo-1:svc:a",
        target_entity="repo-2:svc:b",
        confidence_state=state,
        explanation=None,
    )


def _blast_radius() -> BlastRadius:
    return BlastRadius(
        seed=EntityReference(repository_id="repo-1", node_id="repo-1:repository"),
        direction="downstream",
        max_hops=2,
        impacted_repositories=("repo-2",),
        impacted_apis=("GET /orders",),
        impacted_databases=("orders",),
        impacted_queues=("order-events",),
        relationships=(_insight("verified"), _insight("likely"), _insight("rejected")),
    )


def test_render_uses_narrative_fields_when_present() -> None:
    narrative = {
        "executive_summary": "Changing repo-1 affects repo-2.",
        "direct_impact": "repo-2 calls repo-1's service directly.",
        "risk_summary": "Moderate risk.",
    }

    rendered = render_impact_analysis(_blast_radius(), narrative)

    assert rendered["blast_radius_overview"] == "repo-2 calls repo-1's service directly."
    assert rendered["risk_summary"] == "Moderate risk."
    assert rendered["directly_impacted_repositories"] == ["repo-2"]
    assert rendered["confidence_summary"] == {"high": 1, "medium": 1, "low": 1}
    assert rendered["relationship_count"] == 3
    assert "# Impact Analysis Report" in rendered["markdown"]


def test_render_falls_back_to_computed_summary_when_narrative_is_empty() -> None:
    rendered = render_impact_analysis(_blast_radius(), {})

    assert "entity(ies) impacted" in rendered["executive_summary"]
    assert "1 low-confidence relationship(s)" in rendered["risk_summary"]
    assert rendered["directly_impacted_repositories"] == ["repo-2"]


def test_render_handles_empty_blast_radius() -> None:
    empty = BlastRadius(
        seed=EntityReference(repository_id="repo-1", node_id="repo-1:repository"),
        direction="downstream",
        max_hops=2,
    )

    rendered = render_impact_analysis(empty, {})

    assert rendered["directly_impacted_repositories"] == []
    assert rendered["confidence_summary"] == {"high": 0, "medium": 0, "low": 0}
    assert rendered["relationship_count"] == 0
