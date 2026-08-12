"""Deterministic question grounding — shared by `POST /ask` (a single-shot
answer) and `ConversationService` (the conversational investigation loop
that seeds each new topic with the same real graph facts before an LLM is
ever allowed to reason over them).

Kept in one place, importable by both, so "what counts as an impact
question" and "which repository does this question mean" are decided
exactly once — not classified one way for a first question and a subtly
different way for a follow-up that turns out to name a new repository.

Nothing here calls an LLM. Every field on the returned `AskResponse` is
either read verbatim from a source system (`fact`) or computed
deterministically from the Knowledge Graph (`derived`) — see
`app.schemas.ask`'s own docstring on why that distinction is load-bearing
for this product, not decoration.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.text_relevance import relevance, term_weights, tokenize
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.repository import Repository
from app.schemas.ask import AskAction, AskEvidenceItem, AskImpact, AskResponse
from app.services.engineering_intelligence import dependency_query_service
from app.services.engineering_intelligence.contracts import (
    BlastRadius,
    EntityReference,
    QueryResult,
)
from app.services.engineering_intelligence.impact_analysis_service import compute_blast_radius

_REPO_NODE_ID_RE = re.compile(r"^([0-9a-fA-F-]{36}):repository$")

# Closed vocabulary, same spirit as `app.context.resolvers.freetext`'s own
# patterns — "impact" and "dependency" overlap in that resolver (both trip
# its single `_IMPACT_PATTERN`), but this needs to actually distinguish
# them: a blast-radius answer and a "what depends on this" answer are
# different services with different evidence, not two phrasings of one
# question. Impact is checked first so "what breaks if X changes, given
# what depends on it" — mentioning both — still gets the higher-signal
# blast-radius answer rather than a bare dependency list.
_IMPACT_PATTERN = re.compile(
    r"\b(impact\w*|affect\w*|break\w*|broke\w*|risk\w*|blast[- ]radius|ripple\w*|downstream)\b",
    re.IGNORECASE,
)
_DEPENDENCY_PATTERN = re.compile(r"\bdepend(s|encies|ency)?\b", re.IGNORECASE)

# Matches `ImpactAnalysisPage`/`impact.py`'s own default — a blast radius
# computed at a different depth would not be comparable to the one those
# pages show for the same repository.
_MAX_HOPS = 2


def classify(question: str) -> str:
    """"impact" | "dependency" | "general" — general means "this endpoint
    has no deterministic path for it," not "no repository was found."""
    if _IMPACT_PATTERN.search(question):
        return "impact"
    if _DEPENDENCY_PATTERN.search(question):
        return "dependency"
    return "general"


async def resolve_repository(
    db: AsyncSession, user_id: uuid.UUID, question: str
) -> Repository | None:
    """The repository the question is most likely about, by token-overlap
    relevance of its name against the question text — or None if nothing
    scores above zero. Scoped to `user_id`, same as every other
    repository read (see `GetIndexedRepositoriesTool`'s own docstring on
    why an unscoped read here would leak another account's repositories
    into this account's answer)."""
    result = await db.execute(select(Repository).where(Repository.user_id == user_id))
    repositories = list(result.scalars().all())
    if not repositories:
        return None

    terms = list(tokenize(question))
    if not terms:
        return None

    def match_text(repo: Repository) -> str:
        return f"{repo.name} {repo.full_name}"

    weights = term_weights(terms, [match_text(r) for r in repositories])
    best_repo, best_score = None, 0.0
    for repo in repositories:
        score = relevance(match_text(repo), terms, weights)
        if score > best_score:
            best_repo, best_score = repo, score
    return best_repo


def _severity(blast_radius: BlastRadius) -> str:
    total = (
        len(blast_radius.impacted_repositories)
        + len(blast_radius.impacted_apis)
        + len(blast_radius.impacted_databases)
        + len(blast_radius.impacted_queues)
    )
    if total == 0:
        return "low"
    if total <= 2:
        return "medium"
    return "high"


async def display_names(db: AsyncSession, blast_radius: BlastRadius) -> dict[str, str]:
    """Node id -> human-readable name, for every node the blast radius
    touched. Prefers the subgraph's own `properties.name` (set at index
    time — see `app.indexer.graph.builder.build_graph`'s Repository node);
    falls back to a `Repository` row lookup for a bare `<uuid>:repository`
    id whose graph node predates that property, so a raw id never reaches
    the answer's affected-systems list when a real name is one query
    away."""
    names = {
        n.id: str(n.properties["name"])
        for n in blast_radius.subgraph.nodes
        if n.properties.get("name")
    }

    unresolved_repo_ids: list[uuid.UUID] = []
    for entity_id in (*blast_radius.impacted_repositories, blast_radius.seed.node_id):
        if entity_id in names:
            continue
        match = _REPO_NODE_ID_RE.match(entity_id)
        if match:
            unresolved_repo_ids.append(uuid.UUID(match.group(1)))
    if unresolved_repo_ids:
        result = await db.execute(select(Repository).where(Repository.id.in_(unresolved_repo_ids)))
        for repo in result.scalars():
            names[f"{repo.id}:repository"] = repo.full_name

    return names


def pretty_entity_name(entity_id: str, names: dict[str, str]) -> str:
    if entity_id in names:
        return names[entity_id]
    # No resolved name (a function/API/queue node, or a repository this
    # user doesn't own) — the trailing dotted segment of the node id is
    # still more readable than the full namespaced id.
    return entity_id.rsplit(":", 1)[-1]


async def ground_impact(db: AsyncSession, question: str, repository: Repository) -> AskResponse:
    blast_radius = await compute_blast_radius(
        db,
        Neo4jGraphRepository(get_driver()),
        EntityReference(repository_id=str(repository.id), node_id=f"{repository.id}:repository"),
        max_hops=_MAX_HOPS,
    )
    names = await display_names(db, blast_radius)

    downstream_count = len(blast_radius.impacted_repositories)
    severity = _severity(blast_radius)
    summary = (
        f"{downstream_count} downstream repositor{'y' if downstream_count == 1 else 'ies'} "
        "may be affected."
    )

    why_paths = [
        f"{pretty_entity_name(insight.source_entity, names)} → "
        f"{pretty_entity_name(insight.target_entity, names)} ({insight.relationship_type})"
        for insight in blast_radius.relationships[:3]
    ]
    why = (
        f"{repository.name} reaches {downstream_count} other tracked "
        f"repositor{'y' if downstream_count == 1 else 'ies'} through "
        f"{len(blast_radius.relationships)} relationship(s) within {_MAX_HOPS} hops."
    )
    if why_paths:
        why += " Key paths: " + "; ".join(why_paths) + "."

    evidence = [
        AskEvidenceItem(
            source="Dependency Graph",
            label=(
                f"{len(blast_radius.relationships)} relationship(s) traced across a "
                f"{_MAX_HOPS}-hop blast radius"
            ),
            provenance="derived",
        ),
        AskEvidenceItem(source="GitHub", label=repository.full_name, provenance="fact"),
    ]

    impact = AskImpact(
        severity=severity,
        summary=summary,
        affected_repositories=[
            pretty_entity_name(x, names) for x in blast_radius.impacted_repositories
        ],
        affected_apis=[pretty_entity_name(x, names) for x in blast_radius.impacted_apis],
        affected_databases=[
            pretty_entity_name(x, names) for x in blast_radius.impacted_databases
        ],
        affected_queues=[pretty_entity_name(x, names) for x in blast_radius.impacted_queues],
    )

    return AskResponse(
        status="answered",
        question=question,
        intent="impact",
        resolved_repository_id=str(repository.id),
        resolved_repository_name=repository.name,
        answer=f"Impact assessment — {severity.title()}. {summary}",
        why=why,
        evidence=evidence,
        impact=impact,
        actions=[
            AskAction(
                label="Explore impact",
                kind="explore_impact",
                href=f"/workspace/impact-analysis?repository={repository.id}",
            ),
            AskAction(
                label="View repository",
                kind="view_repository",
                href=f"/repositories/{repository.id}",
            ),
            AskAction(
                label="View dependency graph", kind="view_dependency_graph", href="/architecture"
            ),
        ],
    )


def _dependency_evidence(result: QueryResult) -> list[AskEvidenceItem]:
    items = [
        AskEvidenceItem(
            source="Dependency Graph",
            label=f"{result.total_matched} relationship(s) in Engineering Memory",
            provenance="derived",
        )
    ]
    for insight in result.relationships[:3]:
        items.append(
            AskEvidenceItem(
                source="Dependency Graph",
                label=(
                    f"{insight.source_entity} → {insight.target_entity} "
                    f"({insight.relationship_type}, {insight.confidence_state})"
                ),
                provenance="fact" if insight.confidence_state == "verified" else "derived",
            )
        )
    return items


async def ground_dependency(db: AsyncSession, question: str, repository: Repository) -> AskResponse:
    result = await dependency_query_service.search(db, [repository.id])
    total = result.total_matched
    answer = (
        f"{total} relationship(s) found for {repository.name}."
        if total
        else f"No tracked relationships found yet for {repository.name}."
    )
    why = (
        f"Engineering Memory holds {total} relationship(s) for {repository.name}, each with an "
        "independently tracked confidence state (verified, highly likely, likely, or candidate)."
    )

    return AskResponse(
        status="answered",
        question=question,
        intent="dependency",
        resolved_repository_id=str(repository.id),
        resolved_repository_name=repository.name,
        answer=answer,
        why=why,
        evidence=_dependency_evidence(result),
        impact=None,
        actions=[
            AskAction(
                label="View dependency graph",
                kind="view_dependency_graph",
                href=f"/workspace/dependency-query?repository={repository.id}",
            ),
            AskAction(
                label="View repository",
                kind="view_repository",
                href=f"/repositories/{repository.id}",
            ),
        ],
    )


async def ground(db: AsyncSession, user_id: uuid.UUID, question: str) -> AskResponse:
    """The full deterministic attempt: classify, resolve a repository, and
    ground with the matching service — or `route_to_investigation` if
    nothing applies. This is exactly what `POST /ask` does; factored out
    so `ConversationService` can seed a new investigation topic with it
    too."""
    intent = classify(question)
    repository = await resolve_repository(db, user_id, question) if intent != "general" else None

    if intent == "impact" and repository is not None:
        return await ground_impact(db, question, repository)
    if intent == "dependency" and repository is not None:
        return await ground_dependency(db, question, repository)

    return AskResponse(status="route_to_investigation", question=question, intent="general")
