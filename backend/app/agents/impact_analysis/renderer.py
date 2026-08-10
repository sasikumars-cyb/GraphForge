"""Renders a `BlastRadius` plus the LLM narrative into the Impact
Analysis Agent's result dict. Pure formatting — no retrieval, no
traversal; every value here was already computed by
`ImpactAnalysisService` or the LLM narrative before this module is
called (see `BaseFrontierAgent.run`: `render_response` runs after both).

`BaseFrontierAgent.run` folds this dict into `AgentOutput.result` via
`ResultMapper.to_agent_output` (confidence/evidence/metrics), so this
module never touches `Confidence`/`Evidence` itself.
"""

from __future__ import annotations

from typing import Any

from app.agents.frontier.response_renderer import to_executive_summary, to_markdown
from app.services.engineering_intelligence.contracts import BlastRadius

_HIGH_CONFIDENCE_STATES = frozenset({"verified", "highly_likely"})
_MEDIUM_CONFIDENCE_STATES = frozenset({"likely", "candidate"})
_LOW_CONFIDENCE_STATES = frozenset({"rejected", "conflicting"})


def _str_field(narrative: dict[str, Any], key: str, fallback: str) -> str:
    value = narrative.get(key)
    return value if isinstance(value, str) and value.strip() else fallback


def render_impact_analysis(blast_radius: BlastRadius, narrative: dict[str, Any]) -> dict[str, Any]:
    total_impacted = (
        len(blast_radius.impacted_repositories)
        + len(blast_radius.impacted_apis)
        + len(blast_radius.impacted_databases)
        + len(blast_radius.impacted_queues)
    )
    confidence_summary = {
        "high": sum(
            1 for r in blast_radius.relationships if r.confidence_state in _HIGH_CONFIDENCE_STATES
        ),
        "medium": sum(
            1 for r in blast_radius.relationships if r.confidence_state in _MEDIUM_CONFIDENCE_STATES
        ),
        "low": sum(
            1 for r in blast_radius.relationships if r.confidence_state in _LOW_CONFIDENCE_STATES
        ),
    }

    executive_summary = _str_field(
        narrative,
        "executive_summary",
        f"{total_impacted} entity(ies) impacted across "
        f"{len(blast_radius.impacted_repositories)} repository(ies).",
    )
    direct_impact = _str_field(narrative, "direct_impact", "")
    indirect_impact = _str_field(narrative, "indirect_impact", "")
    high_confidence_summary = _str_field(narrative, "high_confidence_relationships", "")
    medium_confidence_summary = _str_field(narrative, "medium_confidence_relationships", "")
    low_confidence_summary = _str_field(narrative, "low_confidence_relationships", "")
    risk_summary = _str_field(
        narrative,
        "risk_summary",
        f"{confidence_summary['low']} low-confidence relationship(s) among "
        f"{len(blast_radius.relationships)} total.",
    )

    sections: dict[str, str | list[str]] = {
        "Executive Summary": executive_summary,
        "Blast Radius Overview": direct_impact,
        "Directly Impacted Repositories": list(blast_radius.impacted_repositories),
        "Indirectly Impacted APIs": list(blast_radius.impacted_apis),
        "High Risk Components": list(blast_radius.impacted_databases)
        + list(blast_radius.impacted_queues),
        "Risk Summary": risk_summary,
    }

    return {
        "seed_repository_id": blast_radius.seed.repository_id,
        "direction": blast_radius.direction,
        "max_hops": blast_radius.max_hops,
        "executive_summary": (
            to_executive_summary(executive_summary, [direct_impact, indirect_impact])
            if (direct_impact or indirect_impact)
            else executive_summary
        ),
        "blast_radius_overview": direct_impact,
        "directly_impacted_repositories": list(blast_radius.impacted_repositories),
        "indirectly_impacted_apis": list(blast_radius.impacted_apis),
        "indirect_impact_summary": indirect_impact,
        "high_risk_components": list(blast_radius.impacted_databases)
        + list(blast_radius.impacted_queues),
        "high_confidence_summary": high_confidence_summary,
        "medium_confidence_summary": medium_confidence_summary,
        "low_confidence_summary": low_confidence_summary,
        "confidence_summary": confidence_summary,
        "risk_summary": risk_summary,
        "relationship_count": len(blast_radius.relationships),
        "markdown": to_markdown("Impact Analysis Report", sections),
    }
