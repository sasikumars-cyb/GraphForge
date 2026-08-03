"""`build_impact_analysis_prompt` — pure, no I/O."""

from __future__ import annotations

import json

from app.agents.impact_analysis.prompt import build_impact_analysis_prompt
from app.services.engineering_intelligence.contracts import (
    BlastRadius,
    EntityReference,
    RelationshipInsight,
)


def _insight(source: str, target: str, state: str) -> RelationshipInsight:
    return RelationshipInsight(
        relationship_key=f"{source}->{target}",
        relationship_type="CALLS_SERVICE",
        source_entity=source,
        target_entity=target,
        confidence_state=state,
        explanation=None,
    )


def test_prompt_serializes_blast_radius_and_buckets_confidence() -> None:
    blast_radius = BlastRadius(
        seed=EntityReference(repository_id="repo-1", node_id="repo-1:repository"),
        direction="downstream",
        max_hops=2,
        impacted_repositories=("repo-2",),
        impacted_apis=("GET /orders",),
        impacted_databases=("orders",),
        impacted_queues=("order-events",),
        relationships=(
            _insight("repo-1:svc:a", "repo-2:svc:b", "verified"),
            _insight("repo-1:svc:a", "repo-2:svc:c", "likely"),
            _insight("repo-1:svc:a", "repo-2:svc:d", "rejected"),
        ),
    )

    spec = build_impact_analysis_prompt(blast_radius)
    payload = json.loads(spec.user_prompt)

    assert payload["changed_repository"] == "repo-1"
    assert payload["impacted_repositories"] == ["repo-2"]
    assert payload["impacted_apis"] == ["GET /orders"]
    assert len(payload["high_confidence_relationships"]) == 1
    assert len(payload["medium_confidence_relationships"]) == 1
    assert len(payload["low_confidence_relationships"]) == 1
    assert spec.stage == "impact_analysis"


def test_prompt_system_prompt_instructs_grounding_only() -> None:
    blast_radius = BlastRadius(
        seed=EntityReference(repository_id="repo-1", node_id="repo-1:repository"),
        direction="downstream",
        max_hops=2,
    )
    spec = build_impact_analysis_prompt(blast_radius)

    assert "FACTS" in spec.system_prompt
    assert "do not invent" in spec.system_prompt.lower()
