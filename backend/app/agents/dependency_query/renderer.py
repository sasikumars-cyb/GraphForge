"""Renders a `QueryResult` plus the LLM narrative into the Dependency
Query Agent's result dict. Pure formatting — no retrieval; every value
here was already computed by `DependencyQueryService` or the LLM
narrative before this module is called (see `BaseFrontierAgent.run`:
`render_response` runs after both).

`BaseFrontierAgent.run` folds this dict into `AgentOutput.result` via
`ResultMapper.to_agent_output` (confidence/evidence/metrics), so this
module never touches `Confidence`/`Evidence` itself.
"""

from __future__ import annotations

from typing import Any

from app.agents.frontier.response_renderer import to_executive_summary, to_markdown
from app.services.engineering_intelligence.contracts import QueryResult, RelationshipInsight

_HIGH_CONFIDENCE_STATES = frozenset({"verified", "highly_likely"})
_MEDIUM_CONFIDENCE_STATES = frozenset({"likely", "candidate"})
_LOW_CONFIDENCE_STATES = frozenset({"rejected", "conflicting"})


def _str_field(narrative: dict[str, Any], key: str, fallback: str) -> str:
    value = narrative.get(key)
    return value if isinstance(value, str) and value.strip() else fallback


def _list_field(narrative: dict[str, Any], key: str) -> list[str]:
    value = narrative.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _split_by_direction(
    repository_id: str, relationships: tuple[RelationshipInsight, ...]
) -> tuple[list[str], list[str]]:
    prefix = f"{repository_id}:"
    dependencies: list[str] = []
    consumers: list[str] = []
    for insight in relationships:
        label = f"{insight.source_entity} -> {insight.target_entity}"
        if insight.source_entity.startswith(prefix):
            dependencies.append(label)
        elif insight.target_entity.startswith(prefix):
            consumers.append(label)
    return dependencies, consumers


def render_dependency_query(
    repository_id: str, result: QueryResult, narrative: dict[str, Any]
) -> dict[str, Any]:
    dependencies, consumers = _split_by_direction(repository_id, result.relationships)
    confidence_breakdown = {
        "high": sum(
            1 for r in result.relationships if r.confidence_state in _HIGH_CONFIDENCE_STATES
        ),
        "medium": sum(
            1 for r in result.relationships if r.confidence_state in _MEDIUM_CONFIDENCE_STATES
        ),
        "low": sum(1 for r in result.relationships if r.confidence_state in _LOW_CONFIDENCE_STATES),
    }
    verified_relationships = [
        f"{r.source_entity} -> {r.target_entity}"
        for r in result.relationships
        if r.confidence_state == "verified"
    ]
    candidate_relationships = [
        f"{r.source_entity} -> {r.target_entity}"
        for r in result.relationships
        if r.confidence_state == "candidate"
    ]

    executive_summary = _str_field(
        narrative,
        "repository",
        f"{len(dependencies)} dependenc(y/ies), {len(consumers)} consumer(s) found "
        f"for {repository_id}.",
    )
    direct_dependencies_summary = _str_field(narrative, "direct_dependencies", "")
    downstream_consumers_summary = _str_field(narrative, "downstream_consumers", "")
    verified_summary = _str_field(narrative, "verified_relationships", "")
    candidate_summary = _str_field(narrative, "candidate_relationships", "")
    architectural_observations = _list_field(narrative, "architectural_observations")

    sections: dict[str, str | list[str]] = {
        "Executive Summary": executive_summary,
        "Direct Dependencies": dependencies,
        "Downstream Consumers": consumers,
        "Verified Relationships": verified_relationships,
        "Candidate Relationships": candidate_relationships,
        "Architectural Notes": architectural_observations,
    }

    return {
        "repository_id": repository_id,
        "executive_summary": to_executive_summary(executive_summary, architectural_observations),
        "direct_dependencies": dependencies,
        "direct_dependencies_summary": direct_dependencies_summary,
        "downstream_consumers": consumers,
        "downstream_consumers_summary": downstream_consumers_summary,
        "verified_relationships": verified_relationships,
        "verified_relationships_summary": verified_summary,
        "candidate_relationships": candidate_relationships,
        "candidate_relationships_summary": candidate_summary,
        "confidence_breakdown": confidence_breakdown,
        "architectural_notes": architectural_observations,
        "total_matched": result.total_matched,
        "markdown": to_markdown("Dependency Query Report", sections),
    }
