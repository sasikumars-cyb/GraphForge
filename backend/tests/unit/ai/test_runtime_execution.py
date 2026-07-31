"""Unit tests for the pure runtime-execution traversal library
(app.context_pipeline.reasoning.runtime_execution).

No graph database, no mocks beyond constructing plain `GraphPayload`
fixtures directly — every test here is a pure function of its input.
"""

from __future__ import annotations

from app.context_pipeline.reasoning.runtime_execution import (
    CallStep,
    build_call_chains,
)
from app.graph.models import GraphEdge, GraphPayload


def _edge(source: str, target: str, edge_type: str = "CALLS") -> GraphEdge:
    return GraphEdge(source_id=source, target_id=target, type=edge_type)


def test_linear_call_chain() -> None:
    payload = GraphPayload(
        edges=[
            _edge("merge", "add_row_hash"),
            _edge("add_row_hash", "detect_changes"),
            _edge("detect_changes", "execute_merge"),
        ]
    )

    (chain,) = build_call_chains(payload, ["merge"])

    assert chain.entry_point == "merge"
    assert list(chain.steps) == [
        CallStep(source="merge", target="add_row_hash", depth=1),
        CallStep(source="add_row_hash", target="detect_changes", depth=2),
        CallStep(source="detect_changes", target="execute_merge", depth=3),
    ]
    assert chain.terminal_operations == ("execute_merge",)
    assert not chain.truncated
    assert not chain.cycle_detected


def test_branching_call_chain_visits_every_branch() -> None:
    payload = GraphPayload(
        edges=[
            _edge("merge", "validate"),
            _edge("merge", "detect_changes"),
            _edge("validate", "check_schema"),
            _edge("detect_changes", "check_schema"),
        ]
    )

    (chain,) = build_call_chains(payload, ["merge"])

    targets_at_depth_1 = {s.target for s in chain.steps if s.depth == 1}
    assert targets_at_depth_1 == {"validate", "detect_changes"}
    # check_schema is reachable from both branches but visited once (its
    # shallowest depth), not duplicated.
    check_schema_steps = [s for s in chain.steps if s.target == "check_schema"]
    assert len(check_schema_steps) == 1
    assert set(chain.terminal_operations) == {"check_schema"}


def test_self_recursion_does_not_infinite_loop() -> None:
    payload = GraphPayload(edges=[_edge("recurse", "recurse")])

    (chain,) = build_call_chains(payload, ["recurse"])

    assert chain.cycle_detected
    assert list(chain.steps) == [CallStep(source="recurse", target="recurse", depth=1)]
    # The self-call is recorded but never re-expanded, so recurse itself
    # (having been fully explored) has no further, unexplored edge.
    assert chain.terminal_operations == ()


def test_mutual_recursion_cycle_does_not_infinite_loop() -> None:
    payload = GraphPayload(edges=[_edge("a", "b"), _edge("b", "a")])

    (chain,) = build_call_chains(payload, ["a"])

    assert chain.cycle_detected
    assert list(chain.steps) == [
        CallStep(source="a", target="b", depth=1),
        CallStep(source="b", target="a", depth=2),
    ]


def test_disconnected_graph_produces_an_honest_empty_chain() -> None:
    payload = GraphPayload(edges=[_edge("unrelated_a", "unrelated_b")])

    (chain,) = build_call_chains(payload, ["merge"])

    assert chain.entry_point == "merge"
    assert chain.steps == ()
    assert chain.terminal_operations == ("merge",)
    assert not chain.truncated
    assert not chain.cycle_detected


def test_empty_graph_produces_an_honest_empty_chain() -> None:
    payload = GraphPayload()

    (chain,) = build_call_chains(payload, ["merge"])

    assert chain.steps == ()
    assert chain.terminal_operations == ("merge",)


def test_depth_limit_truncates_and_records_the_boundary_edge() -> None:
    payload = GraphPayload(
        edges=[
            _edge("merge", "a"),
            _edge("a", "b"),
            _edge("b", "c"),
        ]
    )

    (chain,) = build_call_chains(payload, ["merge"], max_depth=2)

    assert [s.depth for s in chain.steps] == [1, 2, 3]
    assert chain.truncated
    # "c" was seen (the edge b->c is real) but never expanded past it.
    assert chain.steps[-1].target == "c"
    assert "c" not in {s.source for s in chain.steps}


def test_deterministic_output_ordering_across_repeated_calls() -> None:
    payload = GraphPayload(
        edges=[
            _edge("merge", "z_last_alphabetically"),
            _edge("merge", "a_first_alphabetically"),
        ]
    )

    first = build_call_chains(payload, ["merge"])
    second = build_call_chains(payload, ["merge"])

    assert first == second
    # Order follows edge-list order, not alphabetical or set order.
    assert [s.target for s in first[0].steps] == [
        "z_last_alphabetically",
        "a_first_alphabetically",
    ]


def test_duplicate_calls_edges_are_not_duplicated_in_output() -> None:
    payload = GraphPayload(
        edges=[
            _edge("merge", "detect_changes"),
            _edge("merge", "detect_changes"),
        ]
    )

    (chain,) = build_call_chains(payload, ["merge"])

    assert list(chain.steps) == [CallStep(source="merge", target="detect_changes", depth=1)]


def test_non_calls_edges_are_ignored() -> None:
    payload = GraphPayload(
        edges=[
            _edge("merge", "detect_changes", edge_type="CALLS"),
            _edge("merge", "MergeConfig", edge_type="IMPORTS"),
        ]
    )

    (chain,) = build_call_chains(payload, ["merge"])

    assert [s.target for s in chain.steps] == ["detect_changes"]


def test_dangling_edge_to_an_unknown_node_is_handled_gracefully() -> None:
    """A malformed/incomplete graph — an edge pointing at a component with
    no corresponding node entry, or entirely absent from `payload.nodes` —
    must never raise; the traversal is edge-driven and doesn't require a
    node entry to exist for either endpoint."""
    payload = GraphPayload(edges=[_edge("merge", "ghost_component")])

    (chain,) = build_call_chains(payload, ["merge"])

    assert [s.target for s in chain.steps] == ["ghost_component"]
    assert chain.terminal_operations == ("ghost_component",)


def test_multiple_entry_points_produce_one_chain_each_in_order() -> None:
    payload = GraphPayload(
        edges=[
            _edge("merge", "detect_changes"),
            _edge("validate", "check_schema"),
        ]
    )

    chains = build_call_chains(payload, ["validate", "merge"])

    assert [c.entry_point for c in chains] == ["validate", "merge"]
    assert [s.target for s in chains[0].steps] == ["check_schema"]
    assert [s.target for s in chains[1].steps] == ["detect_changes"]


def test_build_call_chains_does_not_mutate_the_input_payload() -> None:
    payload = GraphPayload(edges=[_edge("merge", "detect_changes")])
    edges_before = list(payload.edges)

    build_call_chains(payload, ["merge"])

    assert payload.edges == edges_before
