"""Pure-function refinement grounding — the deterministic graph math
(`compute_critical_paths`/`compute_parallelizable`/`compute_downstream_impact`)
and the readiness score. No DB, no LLM, no network — see
`app.services.refinement_grounding`'s own docstring for why these stay
pure functions rather than another LLM guess.
"""

from __future__ import annotations

from app.schemas.refinement import OpenQuestion, WorkItem, WorkItemEdge
from app.services.refinement_grounding import (
    compute_critical_paths,
    compute_downstream_impact,
    compute_parallelizable,
    compute_readiness,
)


def _item(item_id: str) -> WorkItem:
    return WorkItem(id=item_id, type="story", status="proposed", title=item_id)


def _edge(source: str, target: str, relationship: str) -> WorkItemEdge:
    return WorkItemEdge(source_id=source, target_id=target, relationship=relationship)


class TestDownstreamImpact:
    def test_blocks_propagates_forward(self) -> None:
        edges = [_edge("A", "B", "blocks"), _edge("B", "C", "blocks")]
        assert compute_downstream_impact(edges, "A") == ["B", "C"]

    def test_depends_on_propagates_from_the_depended_upon_item(self) -> None:
        # C depends_on B: B slipping puts C at risk, not the other way round.
        edges = [_edge("C", "B", "depends_on")]
        assert compute_downstream_impact(edges, "B") == ["C"]
        assert compute_downstream_impact(edges, "C") == []

    def test_unrelated_edge_types_do_not_propagate(self) -> None:
        edges = [_edge("A", "B", "related"), _edge("A", "C", "enables")]
        assert compute_downstream_impact(edges, "A") == []

    def test_leaf_item_has_no_downstream(self) -> None:
        edges = [_edge("A", "B", "blocks")]
        assert compute_downstream_impact(edges, "B") == []

    def test_empty_edges_never_errors(self) -> None:
        assert compute_downstream_impact([], "anything") == []


class TestCriticalPaths:
    def test_single_unambiguous_chain(self) -> None:
        items = [_item(i) for i in ("A", "B", "C")]
        edges = [_edge("A", "B", "blocks"), _edge("B", "C", "blocks")]
        assert compute_critical_paths(items, edges) == [["A", "B", "C"]]

    def test_no_edges_means_no_critical_path(self) -> None:
        items = [_item(i) for i in ("A", "B")]
        assert compute_critical_paths(items, []) == []

    def test_tied_branches_both_reported(self) -> None:
        # SPIKE -> A -> B -> {C, D} — two equally-long chains through B.
        items = [_item(i) for i in ("SPIKE", "A", "B", "C", "D")]
        edges = [
            _edge("SPIKE", "A", "blocks"),
            _edge("A", "B", "blocks"),
            _edge("B", "C", "blocks"),
            _edge("B", "D", "blocks"),
        ]
        paths = compute_critical_paths(items, edges)
        assert sorted(paths) == [["SPIKE", "A", "B", "C"], ["SPIKE", "A", "B", "D"]]

    def test_parent_child_and_related_do_not_form_a_critical_path(self) -> None:
        items = [_item(i) for i in ("EPIC", "STORY")]
        edges = [_edge("EPIC", "STORY", "parent_child")]
        assert compute_critical_paths(items, edges) == []


class TestParallelizable:
    def test_items_with_no_precedence_edges_are_parallelizable(self) -> None:
        items = [_item(i) for i in ("A", "B", "C")]
        edges = [_edge("A", "B", "blocks")]
        assert compute_parallelizable(items, edges) == ["C"]

    def test_related_and_parent_child_do_not_block_parallelizability(self) -> None:
        items = [_item(i) for i in ("A", "B")]
        edges = [_edge("A", "B", "related")]
        assert compute_parallelizable(items, edges) == ["A", "B"]

    def test_no_edges_means_everything_is_parallelizable(self) -> None:
        items = [_item(i) for i in ("A", "B", "C")]
        assert compute_parallelizable(items, []) == ["A", "B", "C"]


class TestReadiness:
    def test_fully_grounded_plan_with_no_unknowns_is_ready(self) -> None:
        readiness = compute_readiness(
            objective="Migrate customer records",
            work_items=[_item("PROPOSED-01")],
            engineering_context_grounded=True,
            open_questions=[OpenQuestion(question="q", category="known")],
        )
        assert readiness.level == "ready"
        assert readiness.score == 100
        assert readiness.investigation_required == []

    def test_missing_objective_and_context_lowers_score_honestly(self) -> None:
        readiness = compute_readiness(
            objective="",
            work_items=[],
            engineering_context_grounded=False,
            open_questions=[],
        )
        assert readiness.score == 25  # only the "no unknowns" criterion is met
        assert readiness.level == "needs_clarification"
        assert "Objective / problem statement" in readiness.needs_clarification
        assert "Work breakdown" in readiness.needs_clarification

    def test_many_unknowns_caps_investigation_required_not_invented(self) -> None:
        unknowns = [
            OpenQuestion(question=f"q{i}", category="unknown") for i in range(3)
        ]
        readiness = compute_readiness(
            objective="x",
            work_items=[_item("PROPOSED-01")],
            engineering_context_grounded=True,
            open_questions=unknowns,
        )
        # 3 unknowns > the 2-question partial-credit threshold — zero credit
        # for that criterion, not a negative or invented score.
        assert readiness.score == 75
        assert len(readiness.investigation_required) == 3

    def test_score_is_never_outside_0_100(self) -> None:
        readiness = compute_readiness(
            objective="",
            work_items=[],
            engineering_context_grounded=False,
            open_questions=[OpenQuestion(question=f"q{i}", category="unknown") for i in range(5)],
        )
        assert 0 <= readiness.score <= 100
        assert readiness.level == "not_ready"
