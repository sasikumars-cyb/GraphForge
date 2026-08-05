"""`Neo4jGraphRepository.get_full_graph`'s bounded (`limit`/`node_types`)
path — real Neo4j writes/reads, no mocks. The unbounded path (both
arguments omitted) is exercised by the many pre-existing callers across
the suite (test_indexing_pipeline.py, test_indexing_api.py, ...) and is
untouched by this change; this file covers only the new behavior.
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
def repository_id() -> str:
    return f"test-bounded-graph-{uuid.uuid4()}"


@pytest.fixture
async def graph_repository(repository_id: str) -> AsyncGenerator[Neo4jGraphRepository, None]:
    repo = Neo4jGraphRepository(get_driver())
    yield repo
    await repo.replace_repository_graph(repository_id, GraphPayload())


def _node(repository_id: str, n: int, label: str = "Component") -> GraphNode:
    return GraphNode(
        id=f"{repository_id}:{label.lower()}-{n}",
        labels=["GraphNode", label],
        properties={"repository_id": repository_id, "name": f"{label}{n}"},
    )


async def _seed_chain(
    graph_repository: Neo4jGraphRepository, repository_id: str, *, count: int, label: str = "Component"
) -> list[GraphNode]:
    """`count` nodes, each linked to the next by CALLS — enough to exercise
    both "some edges land inside the returned page" and "some edges would
    have crossed the page boundary and must be dropped"."""
    nodes = [_node(repository_id, i, label) for i in range(count)]
    edges = [
        GraphEdge(source_id=nodes[i].id, target_id=nodes[i + 1].id, type="CALLS")
        for i in range(count - 1)
    ]
    await graph_repository.replace_repository_graph(
        repository_id, GraphPayload(nodes=nodes, edges=edges)
    )
    return nodes


class TestLimit:
    async def test_no_limit_returns_everything_unbounded(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        await _seed_chain(graph_repository, repository_id, count=5)

        graph = await graph_repository.get_full_graph(repository_id)

        assert len(graph.nodes) == 5
        assert graph.truncated is False
        assert graph.total_node_count is None

    async def test_limit_below_total_truncates_and_reports_the_real_total(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        await _seed_chain(graph_repository, repository_id, count=5)

        graph = await graph_repository.get_full_graph(repository_id, limit=3)

        assert len(graph.nodes) == 3
        assert graph.truncated is True
        assert graph.total_node_count == 5

    async def test_limit_at_or_above_total_is_not_truncated(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        await _seed_chain(graph_repository, repository_id, count=5)

        graph = await graph_repository.get_full_graph(repository_id, limit=5)

        assert len(graph.nodes) == 5
        assert graph.truncated is False

    async def test_edges_to_a_node_outside_the_page_are_dropped(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        """5 nodes chained 0->1->2->3->4; limiting to the first 3 (by id
        order) must keep the 0->1 and 1->2 edges but drop 2->3 (target
        outside the page) — nothing on screen for a dangling edge to point
        at."""
        await _seed_chain(graph_repository, repository_id, count=5)

        graph = await graph_repository.get_full_graph(repository_id, limit=3)

        returned_ids = {n.id for n in graph.nodes}
        assert all(e.source_id in returned_ids and e.target_id in returned_ids for e in graph.edges)
        assert len(graph.edges) == 2

    async def test_requested_limit_above_the_hard_ceiling_is_capped_not_rejected(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        """A caller asking for more than the server's hard ceiling gets the
        ceiling silently applied, not an error — same defense-in-depth
        posture as get_neighborhood's max_hops bound."""
        await _seed_chain(graph_repository, repository_id, count=5)

        graph = await graph_repository.get_full_graph(repository_id, limit=50_000)

        assert len(graph.nodes) == 5
        assert graph.truncated is False

    async def test_empty_repository_with_a_limit_returns_an_empty_untruncated_payload(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        graph = await graph_repository.get_full_graph(repository_id, limit=100)

        assert graph.nodes == []
        assert graph.edges == []
        assert graph.truncated is False


class TestNodeTypes:
    async def test_filters_to_only_the_requested_labels(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        services = [_node(repository_id, i, "Service") for i in range(2)]
        topics = [_node(repository_id, i, "KafkaTopic") for i in range(3)]
        await graph_repository.replace_repository_graph(
            repository_id, GraphPayload(nodes=[*services, *topics], edges=[])
        )

        graph = await graph_repository.get_full_graph(repository_id, node_types=["Service"])

        assert len(graph.nodes) == 2
        assert all("Service" in n.labels for n in graph.nodes)

    async def test_unknown_label_raises_rather_than_silently_matching_nothing(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        with pytest.raises(ValueError, match="Unknown node type"):
            await graph_repository.get_full_graph(repository_id, node_types=["NotARealLabel"])

    async def test_type_filter_and_limit_compose(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        services = [_node(repository_id, i, "Service") for i in range(5)]
        await graph_repository.replace_repository_graph(
            repository_id, GraphPayload(nodes=services, edges=[])
        )

        graph = await graph_repository.get_full_graph(
            repository_id, limit=2, node_types=["Service"]
        )

        assert len(graph.nodes) == 2
        assert graph.truncated is True
        assert graph.total_node_count == 5
