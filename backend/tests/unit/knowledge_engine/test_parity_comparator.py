"""Graph Parity Engine RFC — `compare_graphs`: pure unit tests, no I/O,
no database, no Neo4j. Covers every Phase 6 requirement: identical
graphs, missing/unexpected node and edge, property mismatch, label
mismatch, duplicate node/edge, ignored properties, a larger graph, and
determinism under random input ordering.
"""

from __future__ import annotations

import random

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.knowledge_engine.parity.comparator import compare_graphs
from app.knowledge_engine.parity.ignore_rules import PropertyIgnoreRule
from app.knowledge_engine.parity.report import OverallResult


def _node(node_id: str, labels: list[str] | None = None, **properties: object) -> GraphNode:
    return GraphNode(id=node_id, labels=labels or ["Component"], properties=properties)


def _edge(source: str, target: str, edge_type: str = "CALLS", **properties: object) -> GraphEdge:
    return GraphEdge(source_id=source, target_id=target, type=edge_type, properties=properties)


def test_identical_graphs_pass_with_full_similarity() -> None:
    payload = GraphPayload(
        nodes=[_node("a", name="A"), _node("b", name="B")],
        edges=[_edge("a", "b")],
    )
    report = compare_graphs(payload, payload)

    assert report.overall_result == OverallResult.PASS
    assert report.similarity_percentage == 100.0
    assert report.missing_nodes == report.unexpected_nodes == ()
    assert report.missing_edges == report.unexpected_edges == ()


def test_missing_node_detected() -> None:
    legacy = GraphPayload(nodes=[_node("a"), _node("b")])
    materialized = GraphPayload(nodes=[_node("a")])

    report = compare_graphs(legacy, materialized)

    assert report.overall_result == OverallResult.FAIL
    assert report.missing_nodes == ("b",)
    assert report.unexpected_nodes == ()
    assert report.node_statistics.legacy_count == 2
    assert report.node_statistics.matched_count == 1


def test_unexpected_node_detected() -> None:
    legacy = GraphPayload(nodes=[_node("a")])
    materialized = GraphPayload(nodes=[_node("a"), _node("b")])

    report = compare_graphs(legacy, materialized)

    assert report.overall_result == OverallResult.FAIL
    assert report.unexpected_nodes == ("b",)


def test_missing_edge_detected() -> None:
    nodes = [_node("a"), _node("b")]
    legacy = GraphPayload(nodes=nodes, edges=[_edge("a", "b")])
    materialized = GraphPayload(nodes=nodes, edges=[])

    report = compare_graphs(legacy, materialized)

    assert report.overall_result == OverallResult.FAIL
    assert len(report.missing_edges) == 1
    assert report.missing_edges[0].source_id == "a"
    assert report.missing_edges[0].target_id == "b"


def test_unexpected_edge_detected() -> None:
    nodes = [_node("a"), _node("b")]
    legacy = GraphPayload(nodes=nodes, edges=[])
    materialized = GraphPayload(nodes=nodes, edges=[_edge("a", "b")])

    report = compare_graphs(legacy, materialized)

    assert report.overall_result == OverallResult.FAIL
    assert len(report.unexpected_edges) == 1


def test_node_property_mismatch_detected_with_exact_diff() -> None:
    legacy = GraphPayload(nodes=[_node("a", name="Alpha", is_test=False)])
    materialized = GraphPayload(nodes=[_node("a", name="Beta", is_test=False)])

    report = compare_graphs(legacy, materialized)

    assert report.overall_result == OverallResult.FAIL
    assert len(report.node_mismatches) == 1
    mismatch = report.node_mismatches[0]
    assert mismatch.node_id == "a"
    assert len(mismatch.property_differences) == 1
    diff = mismatch.property_differences[0]
    assert diff.key == "name"
    assert diff.legacy_value == '"Alpha"'
    assert diff.materialized_value == '"Beta"'


def test_node_label_mismatch_detected() -> None:
    legacy = GraphPayload(nodes=[_node("a", labels=["Component", "Service"])])
    materialized = GraphPayload(nodes=[_node("a", labels=["Component"])])

    report = compare_graphs(legacy, materialized)

    assert report.overall_result == OverallResult.FAIL
    assert report.node_mismatches[0].label_differences == ("missing label: Service",)


def test_edge_property_mismatch_reported_distinctly_from_missing_unexpected() -> None:
    nodes = [_node("a"), _node("b")]
    legacy = GraphPayload(nodes=nodes, edges=[_edge("a", "b", via="feign-a")])
    materialized = GraphPayload(nodes=nodes, edges=[_edge("a", "b", via="feign-b")])

    report = compare_graphs(legacy, materialized)

    assert report.overall_result == OverallResult.FAIL
    # Same triple exists on both sides -- this is a property mismatch,
    # not a missing/unexpected edge.
    assert report.missing_edges == ()
    assert report.unexpected_edges == ()
    assert len(report.edge_property_mismatches) == 1
    mismatch = report.edge_property_mismatches[0]
    assert mismatch.property_differences[0].key == "via"


def test_duplicate_node_id_detected() -> None:
    legacy = GraphPayload(nodes=[_node("a"), _node("a")])
    materialized = GraphPayload(nodes=[_node("a")])

    report = compare_graphs(legacy, materialized)

    assert len(report.duplicate_nodes) == 1
    dup = report.duplicate_nodes[0]
    assert dup.key == "a"
    assert dup.legacy_count == 2
    assert dup.materialized_count == 1


def test_duplicate_edge_triple_legitimate_on_both_sides_is_not_a_failure() -> None:
    """Two Kafka-producer methods sharing (source, type, target) with
    different properties is a documented, legitimate pattern
    (materializer.py's own docstring) -- present identically on both
    sides, this must not fail parity."""
    nodes = [_node("a"), _node("b")]
    edges = [
        _edge("a", "b", edge_type="PRODUCES_TO", method="sendOrder"),
        _edge("a", "b", edge_type="PRODUCES_TO", method="sendRefund"),
    ]
    payload = GraphPayload(nodes=nodes, edges=edges)

    report = compare_graphs(payload, payload)

    assert report.overall_result == OverallResult.PASS
    assert len(report.duplicate_edges) == 1
    assert report.duplicate_edges[0].legacy_count == 2
    assert report.duplicate_edges[0].materialized_count == 2


def test_duplicate_edge_count_mismatch_between_sides_is_a_failure() -> None:
    nodes = [_node("a"), _node("b")]
    legacy = GraphPayload(
        nodes=nodes,
        edges=[
            _edge("a", "b", edge_type="PRODUCES_TO", method="sendOrder"),
            _edge("a", "b", edge_type="PRODUCES_TO", method="sendRefund"),
        ],
    )
    materialized = GraphPayload(
        nodes=nodes, edges=[_edge("a", "b", edge_type="PRODUCES_TO", method="sendOrder")]
    )

    report = compare_graphs(legacy, materialized)

    assert report.overall_result == OverallResult.FAIL
    assert len(report.missing_edges) == 1
    assert report.missing_edges[0].properties_json == '{"method": "sendRefund"}'


def test_ignored_property_excluded_from_failure_and_reported_separately() -> None:
    nodes = [_node("a"), _node("b")]
    legacy = GraphPayload(nodes=nodes, edges=[_edge("a", "b", confidence="structural")])
    materialized = GraphPayload(nodes=nodes, edges=[_edge("a", "b", confidence="verified")])

    rules = (PropertyIgnoreRule(applies_to="edge", property_name="confidence", reason="test"),)
    report = compare_graphs(legacy, materialized, ignore_rules=rules)

    assert report.overall_result == OverallResult.PASS
    assert report.edge_property_mismatches == ()
    assert len(report.ignored_differences) == 1
    assert report.ignored_differences[0].property_name == "confidence"


def test_configurable_ignore_mechanism_is_not_hardcoded() -> None:
    """A caller-supplied ignore rule for a property this module has never
    heard of works identically to the built-in ones -- proves the
    mechanism is genuinely configurable, not a disguised hardcoded list."""
    nodes = [_node("a"), _node("b")]
    legacy = GraphPayload(nodes=nodes, edges=[_edge("a", "b", made_up_field="x")])
    materialized = GraphPayload(nodes=nodes, edges=[_edge("a", "b", made_up_field="y")])

    rules = (PropertyIgnoreRule(applies_to="edge", property_name="made_up_field"),)
    report = compare_graphs(legacy, materialized, ignore_rules=rules)

    assert report.overall_result == OverallResult.PASS


def test_ignore_rule_scoped_to_edge_type_does_not_apply_elsewhere() -> None:
    nodes = [_node("a"), _node("b")]
    legacy = GraphPayload(
        nodes=nodes,
        edges=[
            _edge("a", "b", edge_type="CALLS_SERVICE", confidence="structural"),
            _edge("a", "b", edge_type="SHARES_TOPIC", confidence="structural"),
        ],
    )
    materialized = GraphPayload(
        nodes=nodes,
        edges=[
            _edge("a", "b", edge_type="CALLS_SERVICE", confidence="verified"),
            _edge("a", "b", edge_type="SHARES_TOPIC", confidence="rejected"),
        ],
    )
    rules = (
        PropertyIgnoreRule(
            applies_to="edge", property_name="confidence", label_or_type="CALLS_SERVICE"
        ),
    )
    report = compare_graphs(legacy, materialized, ignore_rules=rules)

    assert report.overall_result == OverallResult.FAIL
    assert len(report.edge_property_mismatches) == 1
    assert report.edge_property_mismatches[0].type == "SHARES_TOPIC"


def test_large_graph_is_handled_and_reports_correctly() -> None:
    node_count = 2000
    legacy_nodes = [_node(f"n{i}", index=i) for i in range(node_count)]
    materialized_nodes = [_node(f"n{i}", index=i) for i in range(node_count) if i != 999]
    legacy_edges = [_edge(f"n{i}", f"n{i + 1}") for i in range(node_count - 1)]
    materialized_edges = list(legacy_edges)

    legacy = GraphPayload(nodes=legacy_nodes, edges=legacy_edges)
    materialized = GraphPayload(nodes=materialized_nodes, edges=materialized_edges)

    report = compare_graphs(legacy, materialized)

    assert report.overall_result == OverallResult.FAIL
    assert report.missing_nodes == ("n999",)
    assert report.node_statistics.legacy_count == node_count
    assert report.node_statistics.matched_count == node_count - 1


def test_determinism_regardless_of_input_ordering() -> None:
    nodes = [_node(f"n{i}", index=i) for i in range(50)]
    edges = [_edge(f"n{i}", f"n{(i + 1) % 50}") for i in range(50)]

    ordered = GraphPayload(nodes=nodes, edges=edges)

    shuffled_nodes = list(nodes)
    shuffled_edges = list(edges)
    rng = random.Random(42)
    rng.shuffle(shuffled_nodes)
    rng.shuffle(shuffled_edges)
    shuffled = GraphPayload(nodes=shuffled_nodes, edges=shuffled_edges)

    report_a = compare_graphs(ordered, shuffled)
    report_b = compare_graphs(shuffled, ordered)  # legacy/materialized swapped too

    # Comparing a graph against a reordering of itself must always PASS,
    # and the two reports (order-swapped inputs) must describe the exact
    # same underlying equivalence.
    assert report_a.overall_result == OverallResult.PASS
    assert report_b.overall_result == OverallResult.PASS
    assert report_a.similarity_percentage == report_b.similarity_percentage == 100.0


def test_repeated_calls_with_identical_input_produce_byte_identical_reports() -> None:
    nodes = [_node("a"), _node("b"), _node("a")]  # includes a duplicate on purpose
    edges = [_edge("a", "b"), _edge("a", "b", note="second")]
    legacy = GraphPayload(nodes=nodes, edges=edges)
    materialized = GraphPayload(nodes=[_node("a")], edges=[_edge("a", "b")])

    first = compare_graphs(legacy, materialized)
    second = compare_graphs(legacy, materialized)

    assert first == second
