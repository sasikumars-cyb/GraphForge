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


class TestCursorPagination:
    """ADR 0023 — `after` (keyset pagination on `n.id`)."""

    async def test_first_page_reports_a_next_cursor_when_truncated(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        await _seed_chain(graph_repository, repository_id, count=5)

        graph = await graph_repository.get_full_graph(repository_id, limit=3)

        assert graph.truncated is True
        assert graph.next_cursor == graph.nodes[-1].id

    async def test_next_cursor_is_none_when_not_truncated(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        await _seed_chain(graph_repository, repository_id, count=5)

        graph = await graph_repository.get_full_graph(repository_id, limit=5)

        assert graph.truncated is False
        assert graph.next_cursor is None

    async def test_second_page_continues_from_the_cursor_with_no_overlap(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        await _seed_chain(graph_repository, repository_id, count=5)

        first = await graph_repository.get_full_graph(repository_id, limit=3)
        second = await graph_repository.get_full_graph(
            repository_id, limit=3, after=first.next_cursor
        )

        first_ids = {n.id for n in first.nodes}
        second_ids = {n.id for n in second.nodes}
        assert first_ids.isdisjoint(second_ids)
        assert len(second.nodes) == 2  # 5 total - 3 already consumed
        assert second.truncated is False
        assert second.next_cursor is None

    async def test_pages_together_cover_every_node_exactly_once(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        seeded = await _seed_chain(graph_repository, repository_id, count=10)

        collected: list[str] = []
        cursor: str | None = None
        for _ in range(10):  # generous upper bound on page count, never hit
            page = await graph_repository.get_full_graph(repository_id, limit=4, after=cursor)
            collected.extend(n.id for n in page.nodes)
            if not page.truncated:
                break
            cursor = page.next_cursor

        assert sorted(collected) == sorted(n.id for n in seeded)

    async def test_total_node_count_stays_the_grand_total_across_pages(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        """`total_node_count` must not shrink as the cursor advances — a
        frontend showing "page N of ~total" needs a stable denominator,
        not one that counts down toward the cursor's own position."""
        await _seed_chain(graph_repository, repository_id, count=10)

        first = await graph_repository.get_full_graph(repository_id, limit=4)
        second = await graph_repository.get_full_graph(
            repository_id, limit=4, after=first.next_cursor
        )

        assert first.total_node_count == 10
        assert second.total_node_count == 10

    async def test_cursor_past_the_last_node_returns_an_empty_final_page(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        seeded = await _seed_chain(graph_repository, repository_id, count=3)
        last_id = sorted(n.id for n in seeded)[-1]

        graph = await graph_repository.get_full_graph(repository_id, limit=10, after=last_id)

        assert graph.nodes == []
        assert graph.truncated is False
        assert graph.next_cursor is None


class TestTypeCounts:
    """ADR 0023 — `get_type_counts`/`get_type_counts_for_repositories`."""

    async def test_get_type_counts_returns_real_untruncated_counts(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        services = [_node(repository_id, i, "Service") for i in range(5)]
        topics = [_node(repository_id, i, "KafkaTopic") for i in range(2)]
        await graph_repository.replace_repository_graph(
            repository_id, GraphPayload(nodes=[*services, *topics], edges=[])
        )

        counts = await graph_repository.get_type_counts(repository_id)

        assert counts == {"Service": 5, "KafkaTopic": 2}

    async def test_get_type_counts_excludes_the_base_label(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        await _seed_chain(graph_repository, repository_id, count=3, label="Service")

        counts = await graph_repository.get_type_counts(repository_id)

        assert "GraphNode" not in counts

    async def test_get_type_counts_empty_repository(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        counts = await graph_repository.get_type_counts(repository_id)
        assert counts == {}

    async def test_get_type_counts_for_repositories_groups_by_repository(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        other_repository_id = f"{repository_id}-other"
        await graph_repository.replace_repository_graph(
            repository_id, GraphPayload(nodes=[_node(repository_id, 0, "Service")])
        )
        await graph_repository.replace_repository_graph(
            other_repository_id,
            GraphPayload(
                nodes=[_node(other_repository_id, 0, "Service"), _node(other_repository_id, 1, "Service")]
            ),
        )
        try:
            counts = await graph_repository.get_type_counts_for_repositories(
                [repository_id, other_repository_id]
            )
            assert counts == {
                repository_id: {"Service": 1},
                other_repository_id: {"Service": 2},
            }
        finally:
            await graph_repository.replace_repository_graph(other_repository_id, GraphPayload())

    async def test_get_type_counts_for_repositories_omits_repositories_with_no_nodes(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        never_indexed_id = f"{repository_id}-never-indexed"

        counts = await graph_repository.get_type_counts_for_repositories(
            [repository_id, never_indexed_id]
        )

        assert never_indexed_id not in counts

    async def test_get_type_counts_for_repositories_empty_input(
        self, graph_repository: Neo4jGraphRepository, repository_id: str
    ) -> None:
        counts = await graph_repository.get_type_counts_for_repositories([])
        assert counts == {}
