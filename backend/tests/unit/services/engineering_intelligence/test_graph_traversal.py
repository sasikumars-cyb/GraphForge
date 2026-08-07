"""Unit tests for the sole traversal helper — pure, no I/O beyond a fake
`IGraphRepository`, mirroring how `app.knowledge_engine.parity`'s tests
stay pure against in-memory `GraphPayload`s."""

from __future__ import annotations

import pytest

from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.services.engineering_intelligence.graph_traversal import traverse

pytestmark = pytest.mark.asyncio


class _FakeGraphRepository(IGraphRepository):
    def __init__(self, neighborhood: GraphPayload) -> None:
        self._neighborhood = neighborhood
        self.get_neighborhood_calls: list[tuple[str, list[str], list[str], int]] = []

    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
        raise NotImplementedError

    async def replace_repository_files_subgraph(
        self, repository_id: str, file_paths: list[str], graph: GraphPayload
    ) -> None:
        raise NotImplementedError

    async def get_full_graph(self, repository_id: str) -> GraphPayload:
        raise NotImplementedError

    async def get_nodes_by_label(self, repository_id: str, label: str) -> list[GraphNode]:
        raise NotImplementedError

    async def get_kafka_topic_edges(self, repository_id: str) -> list[GraphEdge]:
        raise NotImplementedError

    async def has_graph(self, repository_id: str) -> bool:
        raise NotImplementedError

    async def replace_cross_repository_edges(
        self, source_repository_id: str, edges: list[GraphEdge]
    ) -> None:
        raise NotImplementedError

    async def get_outgoing_cross_repository_edges(self, repository_id: str) -> list[GraphEdge]:
        raise NotImplementedError

    async def get_neighborhood(
        self,
        repository_id: str,
        seed_node_ids: list[str],
        edge_types: list[str],
        max_hops: int,
    ) -> GraphPayload:
        self.get_neighborhood_calls.append((repository_id, seed_node_ids, edge_types, max_hops))
        return self._neighborhood


def _payload() -> GraphPayload:
    return GraphPayload(
        nodes=[
            GraphNode(id="repo-1:endpoint:/a", labels=["Endpoint"]),
            GraphNode(id="repo-1:endpoint:/b", labels=["Endpoint"]),
            GraphNode(id="repo-1:table:orders", labels=["DataTable"]),
            GraphNode(id="repo-1:svc:checkout", labels=["Component", "Service"]),
        ],
        edges=[
            GraphEdge(
                source_id="repo-1:svc:checkout", target_id="repo-1:table:orders", type="READS_FROM"
            )
        ],
    )


async def test_traverse_groups_nodes_by_label() -> None:
    fake = _FakeGraphRepository(_payload())

    neighborhood = await traverse(
        fake,
        repository_id="repo-1",
        seed_node_ids=["repo-1:svc:checkout"],
        edge_types=["READS_FROM"],
        max_hops=2,
    )

    assert neighborhood.nodes_by_label["Endpoint"] == (
        "repo-1:endpoint:/a",
        "repo-1:endpoint:/b",
    )
    assert neighborhood.nodes_by_label["DataTable"] == ("repo-1:table:orders",)
    assert neighborhood.nodes_by_label["Component"] == ("repo-1:svc:checkout",)
    assert neighborhood.nodes_by_label["Service"] == ("repo-1:svc:checkout",)
    assert neighborhood.payload is fake._neighborhood


async def test_traverse_passes_arguments_through_unchanged() -> None:
    fake = _FakeGraphRepository(GraphPayload())

    await traverse(
        fake,
        repository_id="repo-1",
        seed_node_ids=["seed"],
        edge_types=["CALLS_SERVICE"],
        max_hops=3,
    )

    assert fake.get_neighborhood_calls == [("repo-1", ["seed"], ["CALLS_SERVICE"], 3)]


async def test_traverse_short_circuits_on_empty_seeds_without_querying() -> None:
    fake = _FakeGraphRepository(_payload())

    neighborhood = await traverse(
        fake, repository_id="repo-1", seed_node_ids=[], edge_types=["CALLS_SERVICE"], max_hops=2
    )

    assert neighborhood.payload == GraphPayload()
    assert neighborhood.nodes_by_label == {}
    assert fake.get_neighborhood_calls == []


async def test_traverse_short_circuits_on_empty_edge_types_without_querying() -> None:
    fake = _FakeGraphRepository(_payload())

    neighborhood = await traverse(
        fake, repository_id="repo-1", seed_node_ids=["seed"], edge_types=[], max_hops=2
    )

    assert neighborhood.payload == GraphPayload()
    assert fake.get_neighborhood_calls == []
