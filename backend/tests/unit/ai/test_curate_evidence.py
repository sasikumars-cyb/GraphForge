"""Tests for investigators.curate_evidence — the orchestration that runs
once after the investigation loop exits (see engine.investigate),
fetching a bounded neighborhood around whichever components the ticket
names and turning the ledger's flat `component` facts into
`state.derived["evidence_package"]`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.investigators import _primary_repository, curate_evidence
from app.context_pipeline.reasoning.ledger import Ledger
from app.context_pipeline.reasoning.memory import WorkingContext
from app.graph.models import GraphNode


def _state_with_components(
    components: list[dict], *, target_repo: str | None = "etl-core"
) -> WorkingContext:
    ledger = Ledger()
    ev = ledger.add_evidence(
        provider="graph", action="traverse_architecture_graph", outcome="success", summary="ok"
    )
    for comp in components:
        ledger.add_fact(
            kind="component",
            subject=comp["name"],
            provider="graph",
            evidence_id=ev.evidence_id,
            value=comp,
        )
    if target_repo:
        repo_ev = ledger.add_evidence(
            provider="graph", action="get_indexed_repositories", outcome="success", summary="ok"
        )
        repo_fact = ledger.add_fact(
            kind="repository",
            subject=target_repo,
            provider="graph",
            evidence_id=repo_ev.evidence_id,
            value={"name": target_repo},
        )
        ledger.add_inference(
            kind="repository_candidate",
            statement=target_repo,
            supporting_fact_ids=[repo_fact.fact_id],
            value={"source": "explicit"},
        )
    state = WorkingContext()
    state.ledger = ledger
    state.derived["enriched_text"] = "Fix the exact deduplicator implementation in etl-core."
    return state


def _session_with_graph_repo(graph_repo) -> SessionContext:
    return SessionContext(db=None, user_id=None, graph_repo_override=graph_repo)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_no_components_produces_empty_package_without_touching_the_graph():
    graph_repo = AsyncMock()
    state = _state_with_components([])

    await curate_evidence(state, _session_with_graph_repo(graph_repo))

    assert state.derived["evidence_package"]["items"] == []
    graph_repo.get_neighborhood.assert_not_called()


@pytest.mark.asyncio
async def test_no_matching_anchor_still_produces_a_valid_package_without_a_graph_call():
    graph_repo = AsyncMock()
    components = [
        {
            "id": "etl-core:class:Unrelated",
            "name": "Unrelated",
            "repository": "etl-core",
            "file_path": "x.py",
        },
    ]
    state = _state_with_components(components)
    state.derived["enriched_text"] = "Nothing here matches anything at all."

    await curate_evidence(state, _session_with_graph_repo(graph_repo))

    graph_repo.get_neighborhood.assert_not_called()
    assert "evidence_package" in state.derived


@pytest.mark.asyncio
async def test_anchor_found_fetches_neighborhood_and_populates_hop_distance():
    anchor_id = "etl-core:class:ExactDeduplicator"
    components = [
        {
            "id": anchor_id,
            "name": "ExactDeduplicator",
            "repository": "etl-core",
            "file_path": "src/etl_core/dedup/exact_dedup.py",
            "is_test": False,
        },
    ]
    state = _state_with_components(components)

    graph_repo = AsyncMock()
    seed_node = GraphNode(id=anchor_id, labels=["Component"], properties={"hop_distance": 0})
    graph_repo.get_neighborhood = AsyncMock(
        return_value=type("Payload", (), {"nodes": [seed_node]})()
    )

    await curate_evidence(state, _session_with_graph_repo(graph_repo))

    graph_repo.get_neighborhood.assert_called_once()
    call_args = graph_repo.get_neighborhood.call_args
    assert call_args[0][0] == "etl-core"  # repository_id parsed from the anchor's own node id
    assert call_args[0][1] == [anchor_id]

    package = state.derived["evidence_package"]
    must_modify = [i for i in package["items"] if i["tier"] == "must_modify"]
    assert any(i["name"] == "ExactDeduplicator" for i in must_modify)

    graph_evidence = [e for e in state.ledger.evidence if e.action == "get_neighborhood"]
    assert len(graph_evidence) == 1
    assert graph_evidence[0].outcome == "success"


@pytest.mark.asyncio
async def test_graph_read_failure_degrades_gracefully_instead_of_raising():
    anchor_id = "etl-core:class:ExactDeduplicator"
    components = [
        {
            "id": anchor_id,
            "name": "ExactDeduplicator",
            "repository": "etl-core",
            "file_path": "src/etl_core/dedup/exact_dedup.py",
        },
    ]
    state = _state_with_components(components)

    graph_repo = AsyncMock()
    graph_repo.get_neighborhood = AsyncMock(side_effect=RuntimeError("Neo4j is unreachable"))

    # Must not raise.
    await curate_evidence(state, _session_with_graph_repo(graph_repo))

    assert "evidence_package" in state.derived
    failed_evidence = [e for e in state.ledger.evidence if e.action == "get_neighborhood"]
    assert len(failed_evidence) == 1
    assert failed_evidence[0].outcome == "failed"


@pytest.mark.asyncio
async def test_no_repository_candidate_at_all_skips_graph_call():
    graph_repo = AsyncMock()
    components = [
        {"id": "x:class:Foo", "name": "Foo", "repository": "unknown-repo", "file_path": "foo.py"},
    ]
    state = _state_with_components(components, target_repo=None)

    await curate_evidence(state, _session_with_graph_repo(graph_repo))

    graph_repo.get_neighborhood.assert_not_called()
    assert "evidence_package" in state.derived


class TestPrimaryRepositoryDeterminism:
    """`_primary_repository`'s candidate fallback (no ranking covers any
    candidate) must have well-defined, insertion-order semantics — self-
    review finding: an earlier version built its candidate pool from a
    `set`, so the answer depended on Python's per-process string hash
    randomization rather than which candidate was actually resolved
    first. A same-process repeated-call check can't catch that specific
    bug (one process has one hash seed for its whole lifetime, so a
    `set`-backed version is *also* stable within a single run) — what
    actually distinguishes "insertion order" from "hash order" is
    testing several DIFFERENT candidate insertion orders and confirming
    the result always tracks insertion, not some order a set happened to
    produce for these particular strings.
    """

    @staticmethod
    def _state_with_ordered_candidates(order: list[str]) -> WorkingContext:
        state = _state_with_components(
            [{"id": "x:class:Foo", "name": "Foo", "repository": order[0], "file_path": "foo.py"}],
            target_repo=None,
        )
        for name in order:
            repo_ev = state.ledger.add_evidence(
                provider="graph", action="get_indexed_repositories", outcome="success", summary="ok"
            )
            repo_fact = state.ledger.add_fact(
                kind="repository",
                subject=name,
                provider="graph",
                evidence_id=repo_ev.evidence_id,
                value={"name": name},
            )
            state.ledger.add_inference(
                kind="repository_candidate",
                statement=name,
                supporting_fact_ids=[repo_fact.fact_id],
                value={"source": "explicit"},
            )
        return state

    def test_first_inserted_candidate_wins_regardless_of_insertion_order(self):
        # Same two repository names, opposite insertion order — a
        # set-backed implementation could return either one for either
        # order (depends only on string hashing, not insertion); this
        # implementation must track insertion order exactly.
        state_ab = self._state_with_ordered_candidates(["repo-a", "repo-b"])
        state_ba = self._state_with_ordered_candidates(["repo-b", "repo-a"])

        assert _primary_repository(state_ab) == "repo-a"
        assert _primary_repository(state_ba) == "repo-b"

    def test_stable_across_many_repeated_calls_on_the_same_state(self):
        state = self._state_with_ordered_candidates(["repo-a", "repo-b"])
        results = {_primary_repository(state) for _ in range(20)}
        assert results == {"repo-a"}
