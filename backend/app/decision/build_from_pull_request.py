"""Builds a real `EngineeringDecision` from a real pull request.

Reuses existing, already-shipped deterministic machinery rather than adding a
second implementation of any of it:

  - `IImpactGraphReader.find_nodes_by_file_paths` — the same "map changed
    files to indexed graph nodes" step `ImpactAnalysisEngine` and the Change
    Investigation Agent's `ReadDependencyGraphTool` both already perform.
  - `app.services.engineering_intelligence.impact_analysis_service
    .compute_blast_radius` — the Engineering Intelligence Service Layer's
    blast-radius traversal, confidence-aware by construction because it reads
    every relationship through `relationship_lookup.fetch_with_confidence`.

What this module adds is only the translation from those two into
`EngineeringDecision`'s shape — and one honest refusal: where a structurally
found impacted entity has no persisted `ConfidenceModel` yet (the Materializer
has not run a confidence pass over it), this builder does not invent one. It
records an `OpenQuestion` instead. A fabricated "low confidence" score would
look identical, to every downstream renderer, to a real one — which is
precisely the distinction this whole contract exists to preserve.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.graph.interfaces import IImpactGraphReader
from app.decision.contracts import (
    AffectedEntity,
    ChangeKind,
    ChangeSummary,
    DiffStat,
    EngineeringDecision,
    EntityType,
    GraphEdgeRef,
    OpenQuestion,
    ReviewerAction,
)
from app.decision.merge_rule import derive_merge_recommendation
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.knowledge_relationship import KnowledgeRelationshipRecord
from app.services.engineering_intelligence.contracts import BlastRadius, EntityReference
from app.services.engineering_intelligence.impact_analysis_service import compute_blast_radius

MODEL_VERSION = "1.0.0"

# `TraversalHop`/relationship edge types the Materializer persists that
# describe one entity depending on, or being reachable from, another — the
# vocabulary this builder treats as "confidence-relevant" when matching a
# structurally-found impacted node back to its `KnowledgeRelationshipRecord`.
# Kept local rather than imported from the traversal layer because this is a
# narrower, decision-specific question ("is this relationship type evidence
# that entity X is genuinely affected") than "which edges does a blast-radius
# walk follow" (broader, already defined in `impact_analysis_service`).
_CONFIDENCE_RELEVANT_RELATIONSHIP_TYPES = frozenset(
    {
        "CALLS",
        "CALLS_SERVICE",
        "DEPENDS_ON",
        "DEPENDS_ON_REPOSITORY",
        "SHARES_TOPIC",
        "PRODUCES_TO",
        "CONSUMES_FROM",
        "READS_FROM",
        "WRITES_TO",
    }
)

_LABEL_TO_ENTITY_TYPE: dict[str, EntityType] = {
    "Repository": "service",
    "Endpoint": "capability",
    "DataTable": "database",
    "KafkaTopic": "topic",
}


def _decision_id(pull_request_id: str, commit_sha: str) -> str:
    """Content-addressed, per `EngineeringDecision`'s own docstring: the same
    (pull request, commit) reanalyzed produces the same id, so a re-run is a
    lookup, not a duplicate."""
    digest = hashlib.sha256(f"{pull_request_id}:{commit_sha}:{MODEL_VERSION}".encode()).hexdigest()
    return f"dec:{digest[:24]}"


def _entity_type_for(node: GraphNode | None, fallback: EntityType) -> EntityType:
    if node is None:
        return fallback
    for label in node.labels:
        if label in _LABEL_TO_ENTITY_TYPE:
            return _LABEL_TO_ENTITY_TYPE[label]
    return fallback


def _entity_name_for(node_id: str, node: GraphNode | None) -> str:
    if node is not None:
        name = node.properties.get("name")
        if isinstance(name, str) and name:
            return name
    # No indexed node for this id (it came from BlastRadius's edge-target
    # scan, not the induced subgraph) — the id itself, trimmed to its last
    # segment, is still more legible than the raw namespaced string.
    return node_id.rsplit(":", maxsplit=1)[-1]


async def _confidence_by_node_id(
    memory: EngineeringMemoryService, involved_repository_ids: set[uuid.UUID]
) -> dict[str, ConfidenceModel]:
    """The one place this builder reaches past `RelationshipInsight` (which
    only exposes `confidence_state: str`) to the full `KnowledgeRelationshipRecord`
    — `AffectedEntity.confidence` needs the complete `ConfidenceModel`, not
    just its state, so a renderer can say *why* ("2 independent sources"),
    not only *what*.

    `involved_repository_ids` must be the same set `compute_blast_radius`
    itself queries — origin repository plus every impacted repository — not
    a guess at which single repository "owns" a given node. A cross-
    repository relationship's `KnowledgeRelationshipRecord` is stored under
    the repository whose indexing run *asserted* it (typically the origin:
    "I declare a dependency on repository X"), which is not generally the
    repository the impacted node itself belongs to. Querying only the
    impacted node's own repository would silently miss exactly the
    cross-repository relationships this feature exists to surface.

    Returns a node id -> `ConfidenceModel` map, keeping the strongest
    `ConfidenceState` when more than one record touches the same node (the
    same "preserve the strongest evidence ever seen" principle
    `ConfidenceModel` itself documents). A node id absent from the returned
    map has no computed confidence yet — a real absence, not a zero.
    """
    best_by_node_id: dict[str, KnowledgeRelationshipRecord] = {}
    for repository_uuid in involved_repository_ids:
        records = await memory.get_current_relationships(repository_uuid)
        for record in records:
            if record.relationship_type not in _CONFIDENCE_RELEVANT_RELATIONSHIP_TYPES:
                continue
            for node_id in (record.source_entity, record.target_entity):
                current = best_by_node_id.get(node_id)
                if current is None or _STATE_RANK.get(
                    record.confidence_state, -1
                ) > _STATE_RANK.get(current.confidence_state, -1):
                    best_by_node_id[node_id] = record

    return {
        node_id: ConfidenceModel(
            state=ConfidenceState(record.confidence_state),
            distinct_confirming_source_types=record.distinct_confirming_source_types,
            confirming_source_types=frozenset(record.confirming_source_types),
            max_confirming_reliability_tier=record.max_confirming_reliability_tier,
            contradiction_count=record.contradiction_count,
            computed_at=record.confidence_computed_at,
            formula_version=record.confidence_formula_version,
        )
        for node_id, record in best_by_node_id.items()
    }


# Ordinal ranking for "strongest confidence seen" when more than one
# relationship record touches the same node — mirrors the monotonic
# preference order the `ConfidenceEngine` itself is required to honor
# (see `app.knowledge_engine.contracts.confidence`), duplicated here only as
# a comparison key, never as a re-implementation of the aggregation logic.
_STATE_RANK: dict[str, int] = {
    ConfidenceState.REJECTED.value: 0,
    ConfidenceState.CONFLICTING.value: 1,
    ConfidenceState.CANDIDATE.value: 2,
    ConfidenceState.LIKELY.value: 3,
    ConfidenceState.HIGHLY_LIKELY.value: 4,
    ConfidenceState.VERIFIED.value: 5,
}


def _edges_touching(blast_radius: BlastRadius, node_id: str) -> tuple[GraphEdgeRef, ...]:
    return tuple(
        GraphEdgeRef(from_node_id=edge.source_id, to_node_id=edge.target_id, edge_type=edge.type)
        for edge in blast_radius.subgraph.edges
        if edge.source_id == node_id or edge.target_id == node_id
    )


async def build_decision(
    *,
    db: AsyncSession,
    graph_repository: IGraphRepository,
    impact_graph_reader: IImpactGraphReader,
    repository_id: str,
    pull_request_id: str,
    commit_sha: str,
    changed_files: list[str],
    diff_stat: DiffStat,
    change_kind: ChangeKind = "modification",
    max_hops: int = 2,
) -> EngineeringDecision:
    """Build the `EngineeringDecision` for one (pull_request_id, commit_sha).

    Every `AffectedEntity` this produces has `origin="deterministic"` — this
    builder only ever reads structural graph traversal and persisted
    confidence records, never calls an LLM. A future builder that layers an
    LLM-proposed hypothesis on top (`origin="llm_inferred"`/`"hybrid"`) is a
    genuinely separate concern and does not belong in this function.
    """
    memory = EngineeringMemoryService(db)
    seed_nodes = await impact_graph_reader.find_nodes_by_file_paths(
        repository_id, set(changed_files)
    )

    change_summary = ChangeSummary(
        files_changed=tuple(changed_files),
        capabilities_touched=tuple(sorted(node.id for node in seed_nodes)),
        change_kind=change_kind,
        diff_stat=diff_stat,
    )

    if not seed_nodes:
        # No changed file matched an indexed node — a real, expected outcome
        # (a config file, a doc, a file in an unindexed language), not a
        # traversal failure. Recorded as a non-safety-relevant OpenQuestion
        # rather than silently returning an empty decision: a reviewer can
        # still see that no assessment was possible, and why.
        no_coverage_questions = (
            OpenQuestion(
                question="Does this change affect any downstream service?",
                why_unknown=(
                    f"None of the {len(changed_files)} changed file(s) matched an indexed "
                    "graph node, so no traversal could be attempted."
                ),
                safety_relevant=False,
            ),
        )
        recommendation = derive_merge_recommendation(
            affected_entities=(), open_questions=no_coverage_questions
        )
        return EngineeringDecision(
            decision_id=_decision_id(pull_request_id, commit_sha),
            pull_request_id=pull_request_id,
            commit_sha=commit_sha,
            computed_at=datetime.now(UTC),
            model_version=MODEL_VERSION,
            change_summary=change_summary,
            merge_recommendation=recommendation,
            open_questions=no_coverage_questions,
        )

    # Aggregate blast radius across every seed node. Kept as a manual merge
    # rather than a new "multi-seed" traversal primitive: each seed's radius
    # is independently meaningful (it is one specific changed capability's
    # reach), and the merge only needs to dedupe the impacted-id sets and the
    # relationships collected for them, both of which are already
    # deterministically ordered by their producer.
    impacted_repositories: set[str] = set()
    impacted_apis: set[str] = set()
    impacted_databases: set[str] = set()
    impacted_queues: set[str] = set()
    all_nodes_by_id: dict[str, GraphNode] = {}
    all_edges: set[tuple[str, str, str]] = set()

    for seed in seed_nodes:
        radius = await compute_blast_radius(
            db,
            graph_repository,
            EntityReference(repository_id=repository_id, node_id=seed.id),
            max_hops=max_hops,
        )
        impacted_repositories |= set(radius.impacted_repositories)
        impacted_apis |= set(radius.impacted_apis)
        impacted_databases |= set(radius.impacted_databases)
        impacted_queues |= set(radius.impacted_queues)
        for subgraph_node in radius.subgraph.nodes:
            all_nodes_by_id[subgraph_node.id] = subgraph_node
        for edge in radius.subgraph.edges:
            all_edges.add((edge.source_id, edge.target_id, edge.type))

    merged_edges = [
        GraphEdge(source_id=source, target_id=target, type=edge_type)
        for source, target, edge_type in sorted(all_edges)
    ]
    merged_subgraph = GraphPayload(nodes=list(all_nodes_by_id.values()), edges=merged_edges)
    merged_radius = BlastRadius(
        seed=EntityReference(repository_id=repository_id, node_id=seed_nodes[0].id),
        direction="downstream",
        max_hops=max_hops,
        impacted_repositories=tuple(sorted(impacted_repositories)),
        impacted_apis=tuple(sorted(impacted_apis)),
        impacted_databases=tuple(sorted(impacted_databases)),
        impacted_queues=tuple(sorted(impacted_queues)),
        subgraph=merged_subgraph,
    )

    impacted_ids = sorted(
        (impacted_repositories | impacted_apis | impacted_databases | impacted_queues)
        - {repository_id}
    )

    # Same repository set `compute_blast_radius` itself queries for
    # relationship-confidence: the origin repository plus every repository
    # any seed's traversal reached. Computed once, reused for every impacted
    # node below, rather than one DB round trip per node.
    involved_repository_ids = {uuid.UUID(repository_id)}
    for repo_node_id in impacted_repositories:
        try:
            involved_repository_ids.add(uuid.UUID(repo_node_id.split(":", maxsplit=1)[0]))
        except (ValueError, IndexError):
            continue
    confidence_by_node_id = await _confidence_by_node_id(memory, involved_repository_ids)

    affected_entities: list[AffectedEntity] = []
    open_questions: list[OpenQuestion] = []
    reviewer_actions: list[ReviewerAction] = []

    for node_id in impacted_ids:
        node = all_nodes_by_id.get(node_id)
        entity_type = _entity_type_for(node, fallback="service")
        confidence = confidence_by_node_id.get(node_id)
        entity_name = _entity_name_for(node_id, node)

        if confidence is None:
            # A structural edge exists (the traversal found it), but no
            # ConfidenceModel has been computed for it — an absence, recorded
            # as exactly that, never smoothed into a fabricated CANDIDATE.
            open_questions.append(
                OpenQuestion(
                    question=f"Is {entity_name} actually affected by this change?",
                    why_unknown=(
                        "A structural graph relationship was found, but no confidence "
                        "assessment has been computed for it yet."
                    ),
                    safety_relevant=True,
                    related_entity_id=node_id,
                )
            )
            continue

        affected_entities.append(
            AffectedEntity(
                entity_id=node_id,
                entity_type=entity_type,
                entity_name=entity_name,
                confidence=confidence,
                origin="deterministic",
                relationship_path=_edges_touching(merged_radius, node_id),
            )
        )

        # REJECTED is excluded here for the same reason `merge_rule.in_scope_entities`
        # excludes it from the verdict: "we checked and it is not affected" needs
        # no reviewer to confirm anything, and asking one to would misread an
        # audit record as an open risk.
        needs_confirmation = confidence.state not in (
            ConfidenceState.VERIFIED,
            ConfidenceState.HIGHLY_LIKELY,
            ConfidenceState.REJECTED,
        )
        if needs_confirmation:
            reviewer_actions.append(
                ReviewerAction(
                    action_id=f"confirm:{node_id}",
                    action="confirm_with_owning_team",
                    target=entity_name,
                    reason=(
                        f"Impact on {entity_name} is only {confidence.state.value} — confirm "
                        "before relying on this assessment."
                    ),
                    blocking=True,
                )
            )

    recommendation = derive_merge_recommendation(
        affected_entities=tuple(affected_entities),
        open_questions=tuple(open_questions),
        reviewer_actions=tuple(reviewer_actions),
    )

    return EngineeringDecision(
        decision_id=_decision_id(pull_request_id, commit_sha),
        pull_request_id=pull_request_id,
        commit_sha=commit_sha,
        computed_at=datetime.now(UTC),
        model_version=MODEL_VERSION,
        change_summary=change_summary,
        merge_recommendation=recommendation,
        affected_entities=tuple(affected_entities),
        open_questions=tuple(open_questions),
        reviewer_actions=tuple(reviewer_actions),
    )
