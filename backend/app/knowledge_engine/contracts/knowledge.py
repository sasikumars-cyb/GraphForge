"""Knowledge — the terminal shape a validated, confidence-scored set of
hypotheses is promoted into. This is what `IGraphRepository` eventually
syncs into `GraphNode`/`GraphEdge` (ADR 0018: "the current graph is always
derived from Engineering Memory, never the other way around"); it is
deliberately not a graph-store shape itself — `app.graph.models.GraphEdge`
already exists and is reused unmodified for that, per ADR 0018's decision
not to introduce a redundant `KnowledgeGraphWriter` interface.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.knowledge_engine.contracts.confidence import ConfidenceModel
from app.knowledge_engine.contracts.provenance import Provenance


@dataclass(frozen=True)
class KnowledgeRelationship:
    """One promoted, graph-facing fact. `hypothesis_ids` and `provenance`
    are both plural: more than one generator (a deterministic parser and an
    LLM, or two independent LLM providers) can independently produce
    hypotheses that converge on the same relationship — this shape
    preserves that it was multiply attested rather than collapsing to a
    single, arbitrarily-chosen origin.
    """

    id: str
    relationship_type: str
    source_entity: str
    target_entity: str
    confidence: ConfidenceModel
    hypothesis_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("KnowledgeRelationship.id must not be empty")
        if not self.relationship_type.strip():
            raise ValueError("KnowledgeRelationship.relationship_type must not be empty")
        if not self.source_entity.strip():
            raise ValueError("KnowledgeRelationship.source_entity must not be empty")
        if not self.target_entity.strip():
            raise ValueError("KnowledgeRelationship.target_entity must not be empty")
        if not self.hypothesis_ids:
            raise ValueError(
                "KnowledgeRelationship.hypothesis_ids must not be empty — "
                "a promoted relationship must trace back to at least one hypothesis"
            )
        if not self.provenance:
            raise ValueError("KnowledgeRelationship.provenance must not be empty")
