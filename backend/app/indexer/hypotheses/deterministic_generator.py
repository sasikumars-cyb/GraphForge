"""ADR 0018 RFC-02 — the existing deterministic parsers
(`SpringBootJavaParser`, `PythonParser`) as `HypothesisGenerator`
implementations, shadow mode: this module produces `Hypothesis` objects,
it never writes to a graph repository.

Reuse decision (see this module's docstring for the "why", not just the
"what"): rather than re-deriving relationship topology from an
`ArchitectureModel` a second time, `architecture_model_to_evidence_pack`
calls `app.indexer.graph.builder.build_graph` — the existing, already-
tested single source of truth for how a discovered entity becomes a node
or edge — and converts its `GraphPayload` into `EvidenceItem`/`Hypothesis`
objects. Re-implementing `build_graph`'s edge-resolution logic here
(ambiguous Python call-name handling, cross-module base-class resolution,
the generic-component fallback for Kafka usages with no Spring
stereotype) would be a parallel implementation of logic that already
exists and is already correct — exactly what the ADR 0018 implementation
rules forbid. This also makes "lossless round-trip" precisely defined and
testable: every `Hypothesis` this module produces is a 1:1 re-encoding of
one `GraphPayload` edge, not an independently-fallible second derivation
that happens to usually agree.

`Hypothesis.relationship_type`/node ids deliberately reuse `graph/
builder.py`'s existing vocabulary (`CONTAINS`, `EXPOSES`, `CALLS`,
`PRODUCES_TO`, `CONSUMES_FROM`, `DEPENDS_ON`, `IMPORTS`, `INHERITS_FROM`,
`READS_FROM`, `WRITES_TO`, and the `f"{repository_id}:{kind}:{key}"` node
id scheme) rather than inventing a second vocabulary — so a later RFC's
promotion of a validated `KnowledgeRelationship` back into `GraphNode`/
`GraphEdge` needs no id/type translation layer.

`Provenance.run_id` is set to the evidence pack's own `id` for now. RFC-02
has exactly one generator producing exactly one pack per parse — there is
no orchestration layer yet that runs multiple generators against a shared
pack and needs to tell their outputs apart by a distinct run identifier.
Introducing a real run-id-minting mechanism belongs to whichever future
RFC first wires multiple generators together against one pack (RFC-06 and
later); inventing one now, with nothing to disambiguate, would be exactly
the kind of speculative infrastructure ADR 0018's implementation rules
reject.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.indexer.graph.builder import build_graph
from app.indexer.models.architecture import ArchitectureModel
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.hypothesis import Hypothesis, HypothesisGenerator
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance

_NODE_KIND_PREFIX = "graph_node"
_EDGE_KIND_PREFIX = "graph_edge"

# A deterministic parser's own literal finding (an annotation it read, a
# structural fact about the AST) is the most reliable evidence class this
# platform will ever produce — see ADR 0018's evidence-reliability
# discussion. No other evidence source (LLM, docs, infra) is expected to
# outrank it; RFC-02 introduces no other source to compare it against yet.
_DETERMINISTIC_RELIABILITY_TIER = 3


def _node_evidence_id(repository_id: str, node: GraphNode) -> str:
    # `node.id` is already deterministic and namespaced (ADR 0007) — no
    # separate hash needed, re-running the same parse produces the same id.
    return f"evidence:{repository_id}:node:{node.id}"


def _edge_evidence_id(repository_id: str, index: int, edge: GraphEdge) -> str:
    # `index` disambiguates edges that legitimately share the same
    # (source, type, target) triple with different properties — e.g. two
    # Kafka producer methods on the same class sending to the same topic.
    # `GraphPayload.edges` order is deterministic (build_graph iterates the
    # model in a fixed order), so the same model always yields the same ids.
    return f"evidence:{repository_id}:edge:{index}:{edge.source_id}:{edge.type}:{edge.target_id}"


def _node_evidence_item(
    *, repository_id: str, commit_sha: str, node: GraphNode, provenance: Provenance
) -> EvidenceItem:
    file_path = str(node.properties.get("file_path", node.id))
    return EvidenceItem(
        id=_node_evidence_id(repository_id, node),
        kind=f"{_NODE_KIND_PREFIX}:{':'.join(node.labels)}",
        source_type="code",
        reliability_tier=_DETERMINISTIC_RELIABILITY_TIER,
        reference=EvidenceReference(
            repository_id=repository_id,
            source_type="code",
            locator=file_path,
            key=node.id,
            commit_sha=commit_sha,
        ),
        raw_value=json.dumps(node.properties, sort_keys=True, default=str),
        provenance=provenance,
    )


def _edge_evidence_item(
    *,
    repository_id: str,
    commit_sha: str,
    index: int,
    edge: GraphEdge,
    provenance: Provenance,
) -> EvidenceItem:
    return EvidenceItem(
        id=_edge_evidence_id(repository_id, index, edge),
        kind=f"{_EDGE_KIND_PREFIX}:{edge.type}",
        source_type="code",
        reliability_tier=_DETERMINISTIC_RELIABILITY_TIER,
        reference=EvidenceReference(
            repository_id=repository_id,
            source_type="code",
            locator=f"{edge.source_id} -> {edge.target_id}",
            key=f"{edge.source_id}:{edge.type}:{edge.target_id}",
            commit_sha=commit_sha,
        ),
        raw_value=json.dumps(
            {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "type": edge.type,
                "properties": edge.properties,
            },
            sort_keys=True,
            default=str,
        ),
        provenance=provenance,
    )


def node_evidence_items_by_node_id(pack: EngineeringEvidencePack) -> dict[str, EvidenceItem]:
    """The `graph_node:*` items in a pack, indexed by the node id they
    describe (`EvidenceReference.key`, set by `_node_evidence_item`).

    Shared by `DeterministicParserHypothesisGenerator.generate` (which
    only needs the ids) and, as of RFC-03A, the deterministic structural
    validators in `app.knowledge_engine.validators.deterministic_structural`
    (which need the full item, to read node properties) — factored out
    specifically so those validators reuse this lookup rather than
    re-deriving it, per ADR 0018's "reuse existing deterministic logic,
    never duplicate it" discipline.
    """
    return {
        item.reference.key: item
        for item in pack.items
        if item.kind.startswith(f"{_NODE_KIND_PREFIX}:") and item.reference.key is not None
    }


def architecture_model_to_evidence_pack(
    *,
    repository_id: str,
    commit_sha: str,
    model: ArchitectureModel,
    identity: GeneratorIdentity,
    schema_version: str = "v1",
    repository_name: str | None = None,
) -> EngineeringEvidencePack:
    """Convert one parser's `ArchitectureModel` into the evidence pack its
    paired `DeterministicParserHypothesisGenerator` reads from, via the
    existing `build_graph` — see this module's docstring for why that
    reuse, rather than re-deriving topology, is the correct choice.

    `repository_name` (optional, defaults to None like `build_graph`'s own
    parameter) must be passed through identically to whatever
    `index_repository` gave the direct-write `build_graph` call for the
    same run - found missing here via the "authoritative" mode activation's
    shadow-compare investigation: the Repository node's `name` property was
    silently absent from every materialized graph (present only in the
    direct-write path), because this call site never received it. Every
    other node type reads `name` from the parsed model itself, not from an
    argument - the Repository node is the one exception, since its "name"
    is really the caller's own `repository.full_name`, unknowable from
    `ArchitectureModel` alone.
    """
    payload: GraphPayload = build_graph(repository_id, model, repository_name=repository_name)
    pack_id = f"pack:{repository_id}:{commit_sha}:{identity.name}"

    provenance = Provenance(
        generator=identity,
        produced_at=datetime.now(UTC),
        pack_id=pack_id,
        pack_version=schema_version,
        run_id=pack_id,
    )

    items: list[EvidenceItem] = [
        _node_evidence_item(
            repository_id=repository_id, commit_sha=commit_sha, node=node, provenance=provenance
        )
        for node in payload.nodes
    ]
    items.extend(
        _edge_evidence_item(
            repository_id=repository_id,
            commit_sha=commit_sha,
            index=index,
            edge=edge,
            provenance=provenance,
        )
        for index, edge in enumerate(payload.edges)
    )

    return EngineeringEvidencePack(
        id=pack_id,
        repository_id=repository_id,
        commit_sha=commit_sha,
        schema_version=schema_version,
        items=tuple(items),
    )


class DeterministicParserHypothesisGenerator(HypothesisGenerator):
    """Wraps a deterministic language parser's output as a
    `HypothesisGenerator`. Stateless and pack-driven: `generate` reads only
    `pack.items`, reconstructing each `Hypothesis` from the `graph_edge:*`
    items `architecture_model_to_evidence_pack` produced — it never
    re-reads an `ArchitectureModel` directly, so it is a genuine,
    independently-testable implementation of the `HypothesisGenerator`
    port, not a thin wrapper that only works when paired with the exact
    pack instance that constructed it.

    `generator_confidence=1.0` on every produced hypothesis is advisory
    only (per `Hypothesis`'s own contract) — it signals "as certain as a
    deterministic parser's literal reading of the source ever gets," not a
    graph-facing confidence state. Actual `ConfidenceState` promotion
    (`VERIFIED` or otherwise) is computed by a `ConfidenceEngine` from
    `ValidationResult`s, introduced in RFC-03 — this generator does not,
    and must not, decide that itself.
    """

    consumes = frozenset({"code"})

    def __init__(self, identity: GeneratorIdentity) -> None:
        self.identity = identity

    async def generate(self, pack: EngineeringEvidencePack) -> list[Hypothesis]:
        node_evidence_by_node_id = node_evidence_items_by_node_id(pack)
        node_evidence_id_by_node_id = {
            node_id: item.id for node_id, item in node_evidence_by_node_id.items()
        }

        hypotheses: list[Hypothesis] = []
        for item in pack.items:
            if not item.kind.startswith(f"{_EDGE_KIND_PREFIX}:"):
                continue
            edge_fact = json.loads(item.raw_value)
            source_id = edge_fact["source_id"]
            target_id = edge_fact["target_id"]
            relationship_type = edge_fact["type"]

            evidence_refs: list[str] = [item.id]
            for node_id in (source_id, target_id):
                node_evidence_id = node_evidence_id_by_node_id.get(node_id)
                if node_evidence_id is not None and node_evidence_id not in evidence_refs:
                    evidence_refs.append(node_evidence_id)

            hypotheses.append(
                Hypothesis(
                    id=f"hyp:{self.identity.name}:{item.id}",
                    relationship_type=relationship_type,
                    source_entity=source_id,
                    target_entity=target_id,
                    evidence_refs=tuple(evidence_refs),
                    explanation=(
                        f"{self.identity.name} deterministically observed a "
                        f"{relationship_type} relationship from {source_id!r} to "
                        f"{target_id!r}."
                    ),
                    provenance=item.provenance,
                    generator_confidence=1.0,
                )
            )
        return hypotheses
