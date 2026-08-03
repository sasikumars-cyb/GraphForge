"""Builds the `PromptSpec` for the Dependency Query Agent.

Summarizes an already-computed `QueryResult`
(`app.services.engineering_intelligence.contracts.QueryResult`) — never
retrieves, never traverses. Every fact in the prompt came from
`DependencyQueryService.search`, already run by `ServiceExecutor` before
`build_prompt` is ever called (see `BaseFrontierAgent.run`).

Direction (direct dependency vs. downstream consumer) is derived from
which side of the relationship this repository is on — the same edge
convention `app.indexer.graph.builder`/`cross_repo_linker` already write
(edges point from the dependent entity to the thing it depends on): if
this repository is `source_entity`, the target is something it depends
on; if this repository is `target_entity`, the source depends on it.

Confidence bucketing (high/medium/low) uses the same three-set grouping
`app.agents.impact_analysis.prompt` already established over
`RelationshipInsight.confidence_state` — not a new taxonomy.
"""

from __future__ import annotations

import json

from app.agents.frontier.prompt_builder import PromptSpec
from app.agents.llm import STAGE_DEPENDENCY_QUERY
from app.services.engineering_intelligence.contracts import QueryResult, RelationshipInsight

_HIGH_CONFIDENCE_STATES = frozenset({"verified", "highly_likely"})
_MEDIUM_CONFIDENCE_STATES = frozenset({"likely", "candidate"})
_LOW_CONFIDENCE_STATES = frozenset({"rejected", "conflicting"})

_SYSTEM_PROMPT = (
    "You are writing a Dependency Query Report for a software repository, "
    "given its already-computed dependency relationships: what it depends "
    "on, what depends on it, and the confidence behind each relationship. "
    "These are FACTS. Do not recompute, dispute, or invent them, and do "
    "not invent dependencies, consumers, or relationships that are not in "
    "the input.\n\n"
    "Respond as JSON matching exactly this shape:\n"
    "{\n"
    '  "repository": "1 sentence identifying the repository being analyzed",\n'
    '  "direct_dependencies": "1-2 sentences on what this repository depends on",\n'
    '  "downstream_consumers": "1-2 sentences on what depends on this repository, or a note '
    'that none were found",\n'
    '  "relationship_confidence": "1-2 sentences on the overall confidence distribution",\n'
    '  "verified_relationships": "1-2 sentences characterizing the verified relationships, or a '
    'note that none were found",\n'
    '  "candidate_relationships": "1-2 sentences characterizing the low-confidence/candidate '
    'relationships, or a note that none were found",\n'
    '  "architectural_observations": ["notable observations a reviewer would want to know"]\n'
    "}\n\n"
    "Rules:\n"
    "- Ground every statement in the supplied dependency data.\n"
    "- If a category is empty, say so plainly rather than omitting the section or inventing "
    "content.\n"
    "- architectural_observations should be specific (e.g. many low-confidence dependencies, a "
    "repository with consumers but no dependencies of its own), not generic advice."
)


def _split_by_direction(
    repository_id: str, relationships: tuple[RelationshipInsight, ...]
) -> tuple[list[str], list[str]]:
    prefix = f"{repository_id}:"
    dependencies: list[str] = []
    consumers: list[str] = []
    for insight in relationships:
        label = f"{insight.source_entity} -> {insight.target_entity} ({insight.relationship_type})"
        if insight.source_entity.startswith(prefix):
            dependencies.append(label)
        elif insight.target_entity.startswith(prefix):
            consumers.append(label)
    return dependencies, consumers


def _bucket_by_confidence(
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


def build_dependency_query_prompt(repository_id: str, result: QueryResult) -> PromptSpec:
    dependencies, consumers = _split_by_direction(repository_id, result.relationships)
    high, medium, low = _bucket_by_confidence(result.relationships)
    user_prompt = json.dumps(
        {
            "repository_id": repository_id,
            "total_matched": result.total_matched,
            "direct_dependencies": dependencies,
            "downstream_consumers": consumers,
            "verified_relationships": high,
            "medium_confidence_relationships": medium,
            "candidate_relationships": low,
        }
    )
    return PromptSpec(
        system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt, stage=STAGE_DEPENDENCY_QUERY
    )
