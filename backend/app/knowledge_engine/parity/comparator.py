"""`compare_graphs` — the pure Graph Comparator. Reuses `GraphNode`/
`GraphEdge`/`GraphPayload` unmodified (`app.graph.models`); introduces no
new graph model. No I/O of any kind: every input is an already-in-memory
`GraphPayload`, every output is an already-in-memory `ParityReport`. Same
two inputs always produce the exact same report, byte for byte — every
collection returned is built by sorting a deterministic key, never by
iterating a `dict`/`set` in whatever order Python happened to produce
(`node_id` order, `frozenset` order, and Python's hash-randomized string
hashing are all explicitly avoided as sort keys for exactly this reason).

Edge identity is deliberately NOT `(source_id, type, target_id)` alone —
`app.knowledge_engine.materializer`'s own docstring documents that a
single repository's legacy graph can legitimately contain two edges
sharing that triple with different properties (two Kafka-producer methods
on one class, same topic). Edges are therefore compared as a *multiset* of
full, ignore-rule-filtered signatures (`EdgeSignature`, including a
canonical JSON encoding of properties) via `collections.Counter` — the
one data structure that makes "missing 2 of these, unexpected 1 of those"
well-defined regardless of how many instances of a triple either side has.
"""

from __future__ import annotations

import json
from collections import Counter

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.knowledge_engine.parity.ignore_rules import (
    DEFAULT_IGNORE_RULES,
    IgnoreRules,
    filter_properties,
)
from app.knowledge_engine.parity.report import (
    DuplicateEntity,
    EdgePropertyMismatch,
    EdgeSignature,
    EdgeStatistics,
    IgnoredDifference,
    NodeMismatch,
    NodeStatistics,
    OverallResult,
    ParityReport,
    PropertyDifference,
)


def _canonical(value: object) -> str:
    """A stable, hashable encoding of a properties dict — sorted keys, no
    dependency on Python's hash-randomized default `str`/`repr` for
    non-JSON-native values (falls back to `str`, applied consistently via
    `default=str`, itself deterministic per value)."""
    return json.dumps(value, sort_keys=True, default=str)


def _property_diff(
    legacy: dict[str, object], materialized: dict[str, object]
) -> tuple[PropertyDifference, ...]:
    keys = sorted(set(legacy) | set(materialized))
    diffs = []
    for key in keys:
        if key not in legacy:
            diffs.append(PropertyDifference(key, None, _canonical(materialized[key])))
        elif key not in materialized:
            diffs.append(PropertyDifference(key, _canonical(legacy[key]), None))
        elif _canonical(legacy[key]) != _canonical(materialized[key]):
            diffs.append(
                PropertyDifference(key, _canonical(legacy[key]), _canonical(materialized[key]))
            )
    return tuple(diffs)


def _dedupe_nodes_by_id(nodes: list[GraphNode]) -> tuple[dict[str, GraphNode], Counter]:
    """Duplicate node ids are a real, detectable condition — `GraphPayload
    .nodes` is a plain list, nothing structurally enforces `GraphNode.id`
    uniqueness. First occurrence wins for comparison purposes; every
    occurrence is counted for duplicate detection."""
    by_id: dict[str, GraphNode] = {}
    counts: Counter = Counter()
    for node in nodes:
        counts[node.id] += 1
        by_id.setdefault(node.id, node)
    return by_id, counts


def _compare_nodes(
    legacy_nodes: list[GraphNode], materialized_nodes: list[GraphNode], ignore_rules: IgnoreRules
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[NodeMismatch, ...],
    tuple[DuplicateEntity, ...],
    tuple[IgnoredDifference, ...],
    NodeStatistics,
]:
    legacy_by_id, legacy_counts = _dedupe_nodes_by_id(legacy_nodes)
    materialized_by_id, materialized_counts = _dedupe_nodes_by_id(materialized_nodes)

    legacy_ids = set(legacy_by_id)
    materialized_ids = set(materialized_by_id)

    missing = tuple(sorted(legacy_ids - materialized_ids))
    unexpected = tuple(sorted(materialized_ids - legacy_ids))

    mismatches: list[NodeMismatch] = []
    ignored: list[IgnoredDifference] = []
    matched = 0
    for node_id in sorted(legacy_ids & materialized_ids):
        legacy_node = legacy_by_id[node_id]
        materialized_node = materialized_by_id[node_id]

        label = legacy_node.labels[0] if legacy_node.labels else ""
        legacy_filtered = filter_properties("node", label, legacy_node.properties, ignore_rules)
        materialized_filtered = filter_properties(
            "node", label, materialized_node.properties, ignore_rules
        )
        for rule in ignore_rules:
            if rule.applies_to != "node":
                continue
            present = (
                rule.property_name in legacy_node.properties
                or rule.property_name in materialized_node.properties
            )
            if present and (rule.label_or_type is None or rule.label_or_type == label):
                ignored.append(IgnoredDifference("node", node_id, rule.property_name, rule.reason))

        label_diffs = tuple(
            sorted(
                [
                    f"missing label: {lbl}"
                    for lbl in set(legacy_node.labels) - set(materialized_node.labels)
                ]
                + [
                    f"unexpected label: {lbl}"
                    for lbl in set(materialized_node.labels) - set(legacy_node.labels)
                ]
            )
        )
        property_diffs = _property_diff(legacy_filtered, materialized_filtered)

        if label_diffs or property_diffs:
            mismatches.append(NodeMismatch(node_id, label_diffs, property_diffs))
        else:
            matched += 1

    duplicates = tuple(
        sorted(
            (
                DuplicateEntity(
                    node_id, legacy_counts.get(node_id, 0), materialized_counts.get(node_id, 0)
                )
                for node_id in legacy_ids | materialized_ids
                if legacy_counts.get(node_id, 0) > 1 or materialized_counts.get(node_id, 0) > 1
            ),
            key=lambda d: d.key,
        )
    )

    stats = NodeStatistics(
        legacy_count=len(legacy_nodes),
        materialized_count=len(materialized_nodes),
        matched_count=matched,
    )
    return missing, unexpected, tuple(mismatches), duplicates, tuple(ignored), stats


def _edge_signature(edge: GraphEdge, ignore_rules: IgnoreRules) -> tuple[tuple, str]:
    filtered = filter_properties("edge", edge.type, edge.properties, ignore_rules)
    triple = (edge.source_id, edge.type, edge.target_id)
    return triple, _canonical(filtered)


def _triple_key(triple: tuple) -> str:
    return "|".join(triple)


def _compare_edges(
    legacy_edges: list[GraphEdge], materialized_edges: list[GraphEdge], ignore_rules: IgnoreRules
) -> tuple[
    tuple[EdgeSignature, ...],
    tuple[EdgeSignature, ...],
    tuple[EdgePropertyMismatch, ...],
    tuple[DuplicateEntity, ...],
    tuple[IgnoredDifference, ...],
    EdgeStatistics,
]:
    legacy_signatures: list[tuple[tuple, str]] = [
        _edge_signature(e, ignore_rules) for e in legacy_edges
    ]
    materialized_signatures: list[tuple[tuple, str]] = [
        _edge_signature(e, ignore_rules) for e in materialized_edges
    ]

    legacy_full_counts: Counter = Counter(legacy_signatures)
    materialized_full_counts: Counter = Counter(materialized_signatures)

    legacy_triple_counts: Counter = Counter(triple for triple, _ in legacy_signatures)
    materialized_triple_counts: Counter = Counter(triple for triple, _ in materialized_signatures)

    # Triples whose overall instance count agrees between both sides are a
    # pure content substitution (same number of edges for this triple,
    # different properties) -- reported via `edge_property_mismatches`
    # below, not here. A triple only belongs in missing/unexpected when
    # the two sides genuinely disagree on how many instances exist.
    balanced_triples = {
        triple
        for triple in set(legacy_triple_counts) | set(materialized_triple_counts)
        if legacy_triple_counts.get(triple, 0) == materialized_triple_counts.get(triple, 0)
        and legacy_triple_counts.get(triple, 0) > 0
    }

    missing_diff = Counter(
        {
            sig: count
            for sig, count in (legacy_full_counts - materialized_full_counts).items()
            if sig[0] not in balanced_triples
        }
    )
    unexpected_diff = Counter(
        {
            sig: count
            for sig, count in (materialized_full_counts - legacy_full_counts).items()
            if sig[0] not in balanced_triples
        }
    )

    def _to_signatures(counter: Counter) -> tuple[EdgeSignature, ...]:
        entries = []
        for (triple, props_json), count in counter.items():
            entries.extend([EdgeSignature(triple[0], triple[2], triple[1], props_json)] * count)
        return tuple(
            sorted(entries, key=lambda s: (s.source_id, s.type, s.target_id, s.properties_json))
        )

    missing = _to_signatures(missing_diff)
    unexpected = _to_signatures(unexpected_diff)

    # Property mismatches: a friendlier view for the common case (a triple
    # present on both sides with the SAME instance count, but different
    # property content) — the raw missing/unexpected multiset above stays
    # the source of truth for correctness; this is a readability layer
    # over it, not a separate computation of what "matches" means.
    property_mismatches: list[EdgePropertyMismatch] = []
    common_triples = set(legacy_triple_counts) & set(materialized_triple_counts)
    for triple in common_triples:
        if legacy_triple_counts[triple] != materialized_triple_counts[triple]:
            continue  # a pure count mismatch, not a content mismatch -- covered above
        legacy_props = sorted(props for t, props in legacy_signatures if t == triple)
        materialized_props = sorted(props for t, props in materialized_signatures if t == triple)
        if legacy_props == materialized_props:
            continue
        for legacy_json, materialized_json in zip(legacy_props, materialized_props, strict=True):
            if legacy_json == materialized_json:
                continue
            property_mismatches.append(
                EdgePropertyMismatch(
                    triple[0],
                    triple[2],
                    triple[1],
                    _property_diff(json.loads(legacy_json), json.loads(materialized_json)),
                )
            )

    property_mismatches.sort(key=lambda m: (m.source_id, m.type, m.target_id))

    duplicates = tuple(
        sorted(
            (
                DuplicateEntity(
                    _triple_key(triple),
                    legacy_triple_counts.get(triple, 0),
                    materialized_triple_counts.get(triple, 0),
                )
                for triple in set(legacy_triple_counts) | set(materialized_triple_counts)
                if legacy_triple_counts.get(triple, 0) > 1
                or materialized_triple_counts.get(triple, 0) > 1
            ),
            key=lambda d: d.key,
        )
    )

    ignored: list[IgnoredDifference] = []
    for triple in common_triples:
        edge_type = triple[1]
        for rule in ignore_rules:
            if rule.applies_to != "edge":
                continue
            if rule.label_or_type is not None and rule.label_or_type != edge_type:
                continue
            has_property = any(
                rule.property_name in edge.properties
                for edge in legacy_edges + materialized_edges
                if (edge.source_id, edge.type, edge.target_id) == triple
            )
            if has_property:
                ignored.append(
                    IgnoredDifference("edge", _triple_key(triple), rule.property_name, rule.reason)
                )

    matched_count = sum((legacy_full_counts & materialized_full_counts).values())
    stats = EdgeStatistics(
        legacy_count=len(legacy_edges),
        materialized_count=len(materialized_edges),
        matched_count=matched_count,
    )
    return (
        missing,
        unexpected,
        tuple(property_mismatches),
        duplicates,
        tuple(sorted(set(ignored), key=lambda i: (i.entity_key, i.property_name))),
        stats,
    )


def _similarity_percentage(node_stats: NodeStatistics, edge_stats: EdgeStatistics) -> float:
    matched = node_stats.matched_count + edge_stats.matched_count
    union = (
        node_stats.legacy_count
        + node_stats.materialized_count
        - node_stats.matched_count
        + edge_stats.legacy_count
        + edge_stats.materialized_count
        - edge_stats.matched_count
    )
    if union <= 0:
        return 100.0
    return round((matched / union) * 100, 2)


def compare_graphs(
    legacy: GraphPayload,
    materialized: GraphPayload,
    *,
    ignore_rules: IgnoreRules = DEFAULT_IGNORE_RULES,
) -> ParityReport:
    """Pure function: no database, no Neo4j, no service/repository access.
    `legacy`/`materialized` are already-in-memory `GraphPayload`s — how
    the caller obtained them (a live Neo4j read, a materializer call, a
    hand-built test fixture) is entirely outside this function's concern."""
    (
        missing_nodes,
        unexpected_nodes,
        node_mismatches,
        duplicate_nodes,
        ignored_node_diffs,
        node_stats,
    ) = _compare_nodes(legacy.nodes, materialized.nodes, ignore_rules)
    (
        missing_edges,
        unexpected_edges,
        edge_property_mismatches,
        duplicate_edges,
        ignored_edge_diffs,
        edge_stats,
    ) = _compare_edges(legacy.edges, materialized.edges, ignore_rules)

    overall_result = (
        OverallResult.PASS
        if not (
            missing_nodes
            or unexpected_nodes
            or node_mismatches
            or missing_edges
            or unexpected_edges
            or edge_property_mismatches
        )
        else OverallResult.FAIL
    )

    return ParityReport(
        overall_result=overall_result,
        node_statistics=node_stats,
        edge_statistics=edge_stats,
        missing_nodes=missing_nodes,
        unexpected_nodes=unexpected_nodes,
        node_mismatches=node_mismatches,
        duplicate_nodes=duplicate_nodes,
        missing_edges=missing_edges,
        unexpected_edges=unexpected_edges,
        edge_property_mismatches=edge_property_mismatches,
        duplicate_edges=duplicate_edges,
        ignored_differences=tuple(
            sorted(
                ignored_node_diffs + ignored_edge_diffs,
                key=lambda i: (i.entity_kind, i.entity_key, i.property_name),
            )
        ),
        similarity_percentage=_similarity_percentage(node_stats, edge_stats),
    )
