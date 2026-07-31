"""`Neo4jGraphRepository.get_neighborhood` against a real Neo4j instance —
no Postgres needed (this method never touches it). Hand-built graphs so
each hop-distance/edge-type assertion is exact and independent of the
parser/indexer pipeline (already covered elsewhere).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def repo() -> AsyncGenerator[Neo4jGraphRepository, None]:
    yield Neo4jGraphRepository(get_driver())


@pytest.fixture
def repository_id() -> str:
    # A fresh, unique repository_id per test — replace_repository_graph
    # deletes anything already under it first, so no separate teardown is
    # needed for this test to be independently re-runnable.
    return f"test-neighborhood-{uuid.uuid4().hex[:12]}"


def _chain_graph(repository_id: str) -> GraphPayload:
    """A CALLS chain: A -> B -> C -> D -> E, plus an unrelated node F with
    no path to any of them at all."""
    node_ids = ["A", "B", "C", "D", "E", "F"]
    nodes = [
        GraphNode(id=f"{repository_id}:{n}", labels=["Component", "Class"], properties={"name": n})
        for n in node_ids
    ]
    edges = [
        GraphEdge(
            source_id=f"{repository_id}:{a}", target_id=f"{repository_id}:{b}", type="CALLS"
        )
        for a, b in [("A", "B"), ("B", "C"), ("C", "D"), ("D", "E")]
    ]
    return GraphPayload(nodes=nodes, edges=edges)


async def test_finds_nodes_within_max_hops_and_reports_correct_distance(
    repo: Neo4jGraphRepository, repository_id: str
) -> None:
    await repo.replace_repository_graph(repository_id, _chain_graph(repository_id))

    result = await repo.get_neighborhood(
        repository_id,
        seed_node_ids=[f"{repository_id}:A"],
        edge_types=["CALLS"],
        max_hops=2,
    )

    by_id = {n.id: n for n in result.nodes}
    assert f"{repository_id}:A" in by_id
    assert by_id[f"{repository_id}:A"].properties["hop_distance"] == 0
    assert by_id[f"{repository_id}:B"].properties["hop_distance"] == 1
    assert by_id[f"{repository_id}:C"].properties["hop_distance"] == 2
    # D is 3 hops away — outside max_hops=2, must not appear at all.
    assert f"{repository_id}:D" not in by_id
    assert f"{repository_id}:E" not in by_id
    # F has no path to A at all — must not appear regardless of max_hops.
    assert f"{repository_id}:F" not in by_id


async def test_traversal_is_undirected(repo: Neo4jGraphRepository, repository_id: str) -> None:
    # Seed from C, in the middle of the A->B->C->D->E chain — B (an
    # ancestor, reached by walking a CALLS edge backwards) must still be
    # found, since a caller of the seed is exactly as relevant as
    # something the seed calls.
    await repo.replace_repository_graph(repository_id, _chain_graph(repository_id))

    result = await repo.get_neighborhood(
        repository_id,
        seed_node_ids=[f"{repository_id}:C"],
        edge_types=["CALLS"],
        max_hops=1,
    )

    by_id = {n.id: n for n in result.nodes}
    assert f"{repository_id}:B" in by_id
    assert f"{repository_id}:D" in by_id


async def test_returns_induced_subgraph_edges(
    repo: Neo4jGraphRepository, repository_id: str
) -> None:
    await repo.replace_repository_graph(repository_id, _chain_graph(repository_id))

    result = await repo.get_neighborhood(
        repository_id,
        seed_node_ids=[f"{repository_id}:A"],
        edge_types=["CALLS"],
        max_hops=2,
    )

    edge_pairs = {(e.source_id, e.target_id) for e in result.edges}
    assert (f"{repository_id}:A", f"{repository_id}:B") in edge_pairs
    assert (f"{repository_id}:B", f"{repository_id}:C") in edge_pairs
    # C->D exists in the full graph but D is outside the neighborhood —
    # the induced subgraph must not include an edge to a node that isn't
    # itself in the result.
    assert not any(
        e.source_id == f"{repository_id}:C"
        for e in result.edges
        if e.target_id == f"{repository_id}:D"
    )


async def test_edge_type_filter_excludes_other_relationship_types(
    repo: Neo4jGraphRepository, repository_id: str
) -> None:
    node_ids = ["A", "B"]
    nodes = [
        GraphNode(id=f"{repository_id}:{n}", labels=["Component", "Class"], properties={"name": n})
        for n in node_ids
    ]
    edges = [
        GraphEdge(source_id=f"{repository_id}:A", target_id=f"{repository_id}:B", type="IMPORTS")
    ]
    await repo.replace_repository_graph(repository_id, GraphPayload(nodes=nodes, edges=edges))

    # Ask only for CALLS — the real edge is IMPORTS. The seed itself is
    # always included ("nodes touched, seeds included" — see the
    # interface docstring), but B must not be reachable through an edge
    # type that wasn't asked for.
    result = await repo.get_neighborhood(
        repository_id, seed_node_ids=[f"{repository_id}:A"], edge_types=["CALLS"], max_hops=2
    )
    node_ids = {n.id for n in result.nodes}
    assert node_ids == {f"{repository_id}:A"}
    assert result.edges == []


async def test_empty_seeds_returns_empty_payload_without_querying(
    repo: Neo4jGraphRepository, repository_id: str
) -> None:
    result = await repo.get_neighborhood(
        repository_id, seed_node_ids=[], edge_types=["CALLS"], max_hops=2
    )
    assert result.nodes == []
    assert result.edges == []


async def test_rejects_out_of_range_max_hops(
    repo: Neo4jGraphRepository, repository_id: str
) -> None:
    with pytest.raises(ValueError):
        await repo.get_neighborhood(
            repository_id, seed_node_ids=[f"{repository_id}:A"], edge_types=["CALLS"], max_hops=99
        )


async def test_multiple_seeds_take_minimum_hop_distance(
    repo: Neo4jGraphRepository, repository_id: str
) -> None:
    await repo.replace_repository_graph(repository_id, _chain_graph(repository_id))

    # D is 3 hops from A, but only 1 hop from a second seed, E's neighbor C.
    result = await repo.get_neighborhood(
        repository_id,
        seed_node_ids=[f"{repository_id}:A", f"{repository_id}:D"],
        edge_types=["CALLS"],
        max_hops=1,
    )
    by_id = {n.id: n for n in result.nodes}
    # C is 1 hop from D (a seed) even though it's 2 hops from A.
    assert by_id[f"{repository_id}:C"].properties["hop_distance"] == 1
