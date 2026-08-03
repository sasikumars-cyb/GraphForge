"""Builds the `PromptSpec` for the Impact Analysis Agent.

Summarizes an already-computed `BlastRadius`
(`app.services.engineering_intelligence.contracts.BlastRadius`) — never
retrieves, never traverses. Every fact in the prompt came from
`ImpactAnalysisService.compute_blast_radius`, already run by
`ServiceExecutor` before `build_prompt` is ever called (see
`BaseFrontierAgent.run`).

Confidence bucketing (high/medium/low) is derived here from
`RelationshipInsight.confidence_state`, the same ranking
`architecture_insight_service._MIN_STATE_RANK` already uses:
verified/highly_likely -> high, likely/candidate -> medium,
rejected/conflicting -> low. This is a presentation grouping only — the
underlying `confidence_state` values are unchanged, real data from
Engineering Memory, not a new confidence computation.
"""

from __future__ import annotations

import json

from app.agents.frontier.prompt_builder import PromptSpec
from app.agents.llm import STAGE_IMPACT_ANALYSIS
from app.services.engineering_intelligence.contracts import BlastRadius, RelationshipInsight

_HIGH_CONFIDENCE_STATES = frozenset({"verified", "highly_likely"})
_MEDIUM_CONFIDENCE_STATES = frozenset({"likely", "candidate"})
_LOW_CONFIDENCE_STATES = frozenset({"rejected", "conflicting"})

_SYSTEM_PROMPT = (
    "You are writing an Impact Analysis Report for a software repository, "
    "given its already-computed blast radius: which repositories, APIs, "
    "databases, and queues it impacts, and the confidence behind each "
    "impacted relationship, already bucketed into high/medium/low "
    "confidence. These are FACTS. Do not recompute, dispute, or invent "
    "them, and do not invent impacted repositories, APIs, databases, "
    "queues, or relationships that are not in the input.\n\n"
    "Respond as JSON matching exactly this shape:\n"
    "{\n"
    '  "executive_summary": "2-3 sentences: what changing this repository would affect",\n'
    '  "changed_component": "1 sentence identifying the repository being analyzed",\n'
    '  "direct_impact": "1-2 sentences on directly impacted repositories/APIs/databases/queues",\n'
    '  "indirect_impact": "1-2 sentences on further-reaching effects, or a note that none were '
    'found",\n'
    '  "high_confidence_relationships": "1-2 sentences characterizing the high-confidence '
    'impacts",\n'
    '  "medium_confidence_relationships": "1-2 sentences characterizing the medium-confidence '
    'impacts",\n'
    '  "low_confidence_relationships": "1-2 sentences characterizing the low-confidence impacts, '
    'or a note that none were found",\n'
    '  "risk_summary": "1-2 sentences: overall risk level of changing this repository"\n'
    "}\n\n"
    "Rules:\n"
    "- Ground every statement in the supplied blast radius.\n"
    "- If a category is empty (e.g. no low-confidence relationships), say so plainly rather than "
    "omitting the section or inventing content.\n"
    "- risk_summary should reflect the actual number and confidence of impacted entities, not "
    "generic caution."
)


def _bucket_relationships(
    relationships: tuple[RelationshipInsight, ...],
) -> tuple[list[str], list[str], list[str]]:
    high: list[str] = []
    medium: list[str] = []
    low: list[str] = []
    for insight in relationships:
        label = f"{insight.source_entity} -> {insight.target_entity} ({insight.relationship_type})"
        if insight.confidence_state in _HIGH_CONFIDENCE_STATES:
            high.append(label)
        elif insight.confidence_state in _MEDIUM_CONFIDENCE_STATES:
            medium.append(label)
        elif insight.confidence_state in _LOW_CONFIDENCE_STATES:
            low.append(label)
    return high, medium, low


def build_impact_analysis_prompt(blast_radius: BlastRadius) -> PromptSpec:
    high, medium, low = _bucket_relationships(blast_radius.relationships)
    user_prompt = json.dumps(
        {
            "changed_repository": blast_radius.seed.repository_id,
            "changed_component": blast_radius.seed.node_id,
            "direction": blast_radius.direction,
            "max_hops": blast_radius.max_hops,
            "impacted_repositories": list(blast_radius.impacted_repositories),
            "impacted_apis": list(blast_radius.impacted_apis),
            "impacted_databases": list(blast_radius.impacted_databases),
            "impacted_queues": list(blast_radius.impacted_queues),
            "high_confidence_relationships": high,
            "medium_confidence_relationships": medium,
            "low_confidence_relationships": low,
        }
    )
    return PromptSpec(
        system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt, stage=STAGE_IMPACT_ANALYSIS
    )
