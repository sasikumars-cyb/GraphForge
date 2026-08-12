"""ADR 0018 RFC-06 — Knowledge Materializer: pure projection from
Engineering Memory (`KnowledgeRelationship`) + `EngineeringEvidencePack`
into `GraphPayload`. Reuses `GraphNode`/`GraphEdge`/`GraphPayload`
unmodified (`app.graph.models`) — the same generic shape `graph/builder.py`
already produces, so `IGraphRepository.replace_repository_graph` needs no
change to accept a materialized payload.

No reasoning happens here — no validators, no confidence computation, no
generators, no LLMs, no source-code access. Every fact this module
produces already exists, verbatim or via one deterministic recovery step,
in Postgres:

- Node ids/labels/properties: read directly from the repository's most
  recent single-repository `EngineeringEvidencePack`'s `graph_node:*`
  items — `deterministic_generator.py`'s `_node_evidence_item` stores a
  node's complete `properties` dict as `raw_value`, so this is a lossless
  read, never a re-derivation.
- Single-repository edge topology/properties: read the same pack's
  `graph_edge:*` items directly, for the same reason — and deliberately
  NOT sourced from `KnowledgeRelationship`, because `graph/builder.py` can
  legitimately produce two edges sharing one `(source_id, type,
  target_id)` triple with different properties (e.g. two Kafka-producer
  methods on the same class, same topic), while `KnowledgeRelationship`'s
  identity is keyed by exactly that triple and only exposes the latest
  version per key — building single-repo edges from it would silently
  collapse such pairs.
- Cross-repository edge topology: read from `get_current_relationships`
  (`KnowledgeRelationship` — CALLS_SERVICE/SHARES_TOPIC/
  DEPENDS_ON_REPOSITORY), since `build_candidate_pack_and_hypotheses`
  produces at most one hypothesis per relationship type per repository
  pair — no multiplicity to lose.
- Cross-repository edge extra properties (`via`, `target_name`, `topics`,
  `dependencies`): recovered from that pair's cross-repo evidence pack.
  `topics` is the one property that needs a computation step (a set
  intersection over the pack's raw per-side topic-usage facts) rather
  than a direct read — the pack deliberately stores every topic each side
  produces/consumes, not the already-intersected answer (see
  `validators/cross_repo.py`'s own docstring). This is pure, deterministic
  data recovery of a value that already exists as a validated fact, not a
  new inference: the relationship's existence and confidence are already
  established by `KnowledgeRelationship` before this ever runs.
- `confidence`: read from `KnowledgeRelationshipRecord.confidence_state`,
  attached to every materialized edge (single- and cross-repository
  alike) as a `confidence` property. This is additive relative to today's
  direct-write paths (`graph/builder.py`'s edges carry no such property;
  `cross_repo_linker.compute_edges` still writes its own legacy
  `structural`/`heuristic` strings, untouched by RFC-05) — a deliberate,
  documented step toward ADR 0018's stated end state ("Neo4j becomes a
  synced, rebuildable projection" of Engineering Memory), not an attempt
  to byte-match the legacy direct-write vocabulary.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack, EvidenceItem
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.knowledge_relationship import KnowledgeRelationshipRecord

_NODE_KIND_PREFIX = "graph_node"
_EDGE_KIND_PREFIX = "graph_edge"
_CROSS_REPO_PACK_PREFIX = "pack:cross_repo:"
# The sentinel every cross-repository evidence pack is stamped with (see
# `validators/cross_repo.py::build_candidate_pack_and_hypotheses`) — used
# to exclude those packs at the SQL level when looking up a repository's
# latest single-repo pack (`_latest_single_repo_pack`).
_CROSS_REPO_COMMIT_SHA = "n/a-cross-repo"

_CROSS_REPO_RELATIONSHIP_TYPES = frozenset(
    {"CALLS_SERVICE", "SHARES_TOPIC", "DEPENDS_ON_REPOSITORY"}
)


def _node_from_evidence_item(item: EvidenceItem) -> GraphNode | None:
    if not item.kind.startswith(f"{_NODE_KIND_PREFIX}:") or item.reference.key is None:
        return None
    labels = item.kind.split(":")[1:]
    properties = json.loads(item.raw_value)
    return GraphNode(id=item.reference.key, labels=labels, properties=properties)


def _relationship_key(relationship_type: str, source_entity: str, target_entity: str) -> tuple:
    return (relationship_type, source_entity, target_entity)


def _confidence_by_relationship(
    records: list[KnowledgeRelationshipRecord],
) -> dict[tuple, str]:
    return {
        _relationship_key(r.relationship_type, r.source_entity, r.target_entity): r.confidence_state
        for r in records
    }


def _single_repo_edges_from_pack(
    pack: EngineeringEvidencePack, confidence_by_relationship: dict[tuple, str]
) -> list[GraphEdge]:
    edges: list[GraphEdge] = []
    for item in pack.items:
        if not item.kind.startswith(f"{_EDGE_KIND_PREFIX}:"):
            continue
        fact = json.loads(item.raw_value)
        properties = dict(fact["properties"])
        confidence = confidence_by_relationship.get(
            _relationship_key(fact["type"], fact["source_id"], fact["target_id"])
        )
        if confidence is not None:
            properties["confidence"] = confidence
        edges.append(
            GraphEdge(
                source_id=fact["source_id"],
                target_id=fact["target_id"],
                type=fact["type"],
                properties=properties,
            )
        )
    return edges


async def _latest_single_repo_pack(
    memory: EngineeringMemoryService, repository_id: uuid.UUID
) -> EngineeringEvidencePack | None:
    """`exclude_commit_sha="n/a-cross-repo"` filters out cross-repository
    packs at the SQL level (not a client-side filter over an already
    `limit`-truncated window) — a repository accumulates one new
    cross-repo pack per pair per account relink, which over enough
    relinks can outnumber its own single-repo packs within any fixed
    `limit`, silently starving this lookup of the row it actually wants.
    """
    records = await memory.list_evidence_packs(
        repository_id, exclude_commit_sha=_CROSS_REPO_COMMIT_SHA
    )
    if not records:
        return None
    return await memory.retrieve_evidence_pack(records[0].pack_id)


def _cross_repo_pack_id(source_repository_id: str, other_repository_id: str) -> str:
    return f"{_CROSS_REPO_PACK_PREFIX}{source_repository_id}:{other_repository_id}"


def _strip_repository_node_id(entity_id: str) -> str:
    return entity_id.rsplit(":repository", 1)[0]


async def _cross_repo_edge_properties(
    memory: EngineeringMemoryService, record: KnowledgeRelationshipRecord
) -> dict[str, object]:
    source_repository_id = _strip_repository_node_id(record.source_entity)
    other_repository_id = _strip_repository_node_id(record.target_entity)
    pack = await memory.retrieve_evidence_pack(
        _cross_repo_pack_id(source_repository_id, other_repository_id)
    )
    if pack is None:
        return {}

    if record.relationship_type == "CALLS_SERVICE":
        via: list[str] = []
        target_name: list[str] = []
        for item in pack.items:
            if item.kind != "feign_client_target_match":
                continue
            fact = json.loads(item.raw_value)
            target_name.append(fact["target_name"])
            via.append(fact["via"])
        return {"via": via, "target_name": target_name}

    if record.relationship_type == "SHARES_TOPIC":
        # The pack deliberately stores every topic each side produces/
        # consumes, not the already-intersected answer (see
        # `validators/cross_repo.py`) — recomputing the intersection here
        # recovers the original `topics` property value; it does not
        # decide whether the relationship exists (already established by
        # `record` itself, before this ever runs).
        source_topics: set[str] = set()
        other_topics: set[str] = set()
        for item in pack.items:
            if item.kind != "kafka_topic_usage":
                continue
            fact = json.loads(item.raw_value)
            if fact["repository_id"] == source_repository_id:
                source_topics.add(fact["topic"])
            elif fact["repository_id"] == other_repository_id:
                other_topics.add(fact["topic"])
        return {"topics": sorted(source_topics & other_topics)}

    if record.relationship_type == "DEPENDS_ON_REPOSITORY":
        dependencies = [
            json.loads(item.raw_value)["dependency_name"]
            for item in pack.items
            if item.kind == "dependency_coordinate_match"
        ]
        return {"dependencies": dependencies}

    return {}


async def _cross_repo_edges(
    memory: EngineeringMemoryService, records: list[KnowledgeRelationshipRecord]
) -> list[GraphEdge]:
    edges: list[GraphEdge] = []
    for record in records:
        if record.relationship_type not in _CROSS_REPO_RELATIONSHIP_TYPES:
            continue
        properties = await _cross_repo_edge_properties(memory, record)
        properties["confidence"] = record.confidence_state
        edges.append(
            GraphEdge(
                source_id=record.source_entity,
                target_id=record.target_entity,
                type=record.relationship_type,
                properties=properties,
            )
        )
    return edges


# RFC-07 promotion gate — the two confidence states ADR 0018's own roadmap
# names for this: "Promotion gated to Verified/Highly Likely only." A
# relationship sitting at Likely/Candidate/Rejected/Conflicting is real,
# explainable Engineering Memory (queryable via the parity/learning APIs),
# but must never silently become authoritative graph truth - this is the
# one place that boundary is enforced for single-repository relationships
# a generator proposed without also pre-baking a `graph_edge` evidence
# item for them (the deterministic generator always does, via
# `architecture_model_to_evidence_pack`, so this never touches its edges;
# see `_RFC07_PROMOTABLE_STATES`'s use below).
_RFC07_PROMOTABLE_STATES = frozenset({"verified", "highly_likely"})


def _relationship_provenance_summary(record: KnowledgeRelationshipRecord) -> dict[str, object]:
    """Best-effort, additive-only properties recovered straight from the
    record itself - no extra I/O, unlike `_cross_repo_edge_properties`
    (which reads a second evidence pack): a generic-generator-promoted
    edge has no evidence pack of its own to recover extra topology-
    specific properties from, only what `KnowledgeRelationship` itself
    already carries (generator identity, explanation)."""
    properties: dict[str, object] = {}
    if record.provenance:
        generator = record.provenance[0].get("generator") if record.provenance[0] else None
        if isinstance(generator, dict) and generator.get("name"):
            properties["generator"] = generator["name"]
            properties["extraction_method"] = generator.get("kind", "")
    if record.explanation:
        summary = record.explanation.get("summary")
        if isinstance(summary, str) and summary:
            properties["explanation"] = summary
    return properties


def _promoted_single_repo_edges(
    current_relationships: list[KnowledgeRelationshipRecord],
    already_covered: frozenset[tuple[str, str, str]],
) -> list[GraphEdge]:
    """Single-repository relationships promoted directly from validated
    Engineering Memory - the RFC-07 activation for any generator that
    doesn't pre-bake its own `graph_edge` evidence item the way the
    deterministic generator does (see `generic_language_generator.py`,
    today's only such generator). Additive only: `already_covered` (every
    `(type, source, target)` triple the pack's own `graph_edge:*` items
    already produced) is never touched or overridden here, so this can
    never collapse the deterministic generator's legitimate same-triple
    multiplicity (see this module's own docstring) - it only ever adds
    edges the pack had nothing to say about at all.
    """
    edges: list[GraphEdge] = []
    for record in current_relationships:
        if record.relationship_type in _CROSS_REPO_RELATIONSHIP_TYPES:
            continue
        if record.confidence_state not in _RFC07_PROMOTABLE_STATES:
            continue
        key = (record.relationship_type, record.source_entity, record.target_entity)
        if key in already_covered:
            continue
        properties = _relationship_provenance_summary(record)
        properties["confidence"] = record.confidence_state
        edges.append(
            GraphEdge(
                source_id=record.source_entity,
                target_id=record.target_entity,
                type=record.relationship_type,
                properties=properties,
            )
        )
    return edges


async def materialize_repository_graph(db: AsyncSession, repository_id: uuid.UUID) -> GraphPayload:
    """Rebuild one repository's `GraphPayload` entirely from Engineering
    Memory + its evidence packs — no parsing, no cloning, no reasoning.
    Returns an empty payload if the repository has never been indexed with
    persistence enabled (nothing to materialize, not an error)."""
    memory = EngineeringMemoryService(db)

    pack = await _latest_single_repo_pack(memory, repository_id)
    current_relationships = await memory.get_current_relationships(repository_id)
    confidence_by_relationship = _confidence_by_relationship(current_relationships)

    nodes: list[GraphNode] = []
    single_repo_edges: list[GraphEdge] = []
    covered_relationship_keys: frozenset[tuple[str, str, str]] = frozenset()
    if pack is not None:
        nodes = [
            node for item in pack.items if (node := _node_from_evidence_item(item)) is not None
        ]
        single_repo_edges = _single_repo_edges_from_pack(pack, confidence_by_relationship)
        covered_relationship_keys = frozenset(
            (e.type, e.source_id, e.target_id) for e in single_repo_edges
        )

    # RFC-07: relationships a generator proposed without pre-baking their
    # own `graph_edge` evidence (see `_promoted_single_repo_edges`'s own
    # docstring) - additive, never overlapping `single_repo_edges` above.
    promoted_edges = _promoted_single_repo_edges(current_relationships, covered_relationship_keys)

    cross_repo_edges = await _cross_repo_edges(memory, current_relationships)

    return GraphPayload(
        nodes=nodes, edges=single_repo_edges + promoted_edges + cross_repo_edges
    )


async def rematerialize_repository_graph(
    db: AsyncSession, graph_repository: IGraphRepository, repository_id: uuid.UUID
) -> GraphPayload:
    """The full projection flow: `materialize_repository_graph` then
    `IGraphRepository.replace_repository_graph` — the two steps ADR 0018's
    diagram shows as one pipeline. Returns the payload written, so a caller
    (e.g. a replay test) can inspect what was materialized without a
    second read."""
    payload = await materialize_repository_graph(db, repository_id)
    await graph_repository.replace_repository_graph(str(repository_id), payload)
    return payload
