"""`ParityReport` — the structured, JSON-serializable output of
`comparator.compare_graphs`. Every collection is a sorted tuple, never a
set/dict-iteration-order-dependent structure — the same discipline
`ConfidenceExplanation` (`app.knowledge_engine.contracts.explanation`)
already uses for exactly the same reason: this report is meant to be
persisted and diffed run-over-run, so its own serialization must be
deterministic independent of input ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OverallResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class PropertyDifference:
    key: str
    legacy_value: str | None
    materialized_value: str | None


@dataclass(frozen=True)
class NodeMismatch:
    node_id: str
    label_differences: tuple[str, ...]  # e.g. "missing label: Service"
    property_differences: tuple[PropertyDifference, ...]


@dataclass(frozen=True)
class EdgeSignature:
    """One edge instance, fully identified — the unit `missing_edges`/
    `unexpected_edges`/`duplicate_edges` are expressed in. Edge identity
    is deliberately NOT just `(source_id, type, target_id)`: single-repo
    edges can legitimately share that triple with different properties
    (documented precedent in `materializer.py` — two Kafka-producer
    methods, same topic), so an edge's full, ignore-rule-filtered
    property set is part of its identity here."""

    source_id: str
    target_id: str
    type: str
    properties_json: str


@dataclass(frozen=True)
class EdgePropertyMismatch:
    """A topologically-identical edge (same source/type/target, present
    on both sides) whose filtered properties differ — the human-readable
    complement to `missing_edges`/`unexpected_edges`'s raw multiset view."""

    source_id: str
    target_id: str
    type: str
    property_differences: tuple[PropertyDifference, ...]


@dataclass(frozen=True)
class DuplicateEntity:
    key: str  # node id, or "source_id|type|target_id" for an edge triple
    legacy_count: int
    materialized_count: int


@dataclass(frozen=True)
class IgnoredDifference:
    entity_kind: str  # "node" | "edge"
    entity_key: str
    property_name: str
    reason: str


@dataclass(frozen=True)
class NodeStatistics:
    legacy_count: int
    materialized_count: int
    matched_count: int


@dataclass(frozen=True)
class EdgeStatistics:
    legacy_count: int
    materialized_count: int
    matched_count: int


@dataclass(frozen=True)
class ParityReport:
    overall_result: OverallResult
    node_statistics: NodeStatistics
    edge_statistics: EdgeStatistics

    missing_nodes: tuple[str, ...] = field(default_factory=tuple)
    unexpected_nodes: tuple[str, ...] = field(default_factory=tuple)
    node_mismatches: tuple[NodeMismatch, ...] = field(default_factory=tuple)
    duplicate_nodes: tuple[DuplicateEntity, ...] = field(default_factory=tuple)

    missing_edges: tuple[EdgeSignature, ...] = field(default_factory=tuple)
    unexpected_edges: tuple[EdgeSignature, ...] = field(default_factory=tuple)
    edge_property_mismatches: tuple[EdgePropertyMismatch, ...] = field(default_factory=tuple)
    duplicate_edges: tuple[DuplicateEntity, ...] = field(default_factory=tuple)

    ignored_differences: tuple[IgnoredDifference, ...] = field(default_factory=tuple)

    similarity_percentage: float = 100.0

    @property
    def summary(self) -> str:
        if self.overall_result == OverallResult.PASS:
            return (
                f"PASS — {self.node_statistics.matched_count}/"
                f"{self.node_statistics.legacy_count} nodes and "
                f"{self.edge_statistics.matched_count}/{self.edge_statistics.legacy_count} "
                f"edges match ({self.similarity_percentage:.2f}% similarity)."
            )
        problems = (
            len(self.missing_nodes)
            + len(self.unexpected_nodes)
            + len(self.node_mismatches)
            + len(self.missing_edges)
            + len(self.unexpected_edges)
            + len(self.edge_property_mismatches)
        )
        return (
            f"FAIL — {problems} unexplained difference(s) "
            f"({self.similarity_percentage:.2f}% similarity)."
        )
