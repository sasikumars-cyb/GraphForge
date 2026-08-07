"""`Neo4jGraphRepository.replace_repository_files_subgraph` — KAN-32's
scoped-delete-and-merge primitive — against a real Neo4j instance. No
mocks: proves the actual Cypher deletes exactly the intended nodes and
nothing else.
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
    return f"test-files-subgraph-{uuid.uuid4()}"


@pytest.fixture
async def graph_repository(repository_id: str) -> AsyncGenerator[Neo4jGraphRepository, None]:
    repo = Neo4jGraphRepository(get_driver())
    yield repo
    await repo.replace_repository_graph(repository_id, GraphPayload())


def _component(repository_id: str, key: str, file_path: str) -> GraphNode:
    return GraphNode(
        id=f"{repository_id}:component:{key}",
        labels=["Component"],
        properties={"name": key, "file_path": file_path},
    )


async def test_scoped_update_replaces_only_the_named_files_nodes(
    repository_id: str, graph_repository: Neo4jGraphRepository
) -> None:
    a = _component(repository_id, "A", "app/a.py")
    b = _component(repository_id, "B", "app/b.py")
    c = _component(repository_id, "C", "app/c.py")
    initial = GraphPayload(
        nodes=[a, b, c],
        edges=[GraphEdge(source_id=a.id, target_id=b.id, type="CALLS")],
    )
    await graph_repository.replace_repository_graph(repository_id, initial)

    # Re-parse of app/a.py only: A is now named "A2".
    a2 = _component(repository_id, "A2", "app/a.py")
    update = GraphPayload(nodes=[a2], edges=[])
    await graph_repository.replace_repository_files_subgraph(repository_id, ["app/a.py"], update)

    graph = await graph_repository.get_full_graph(repository_id)
    node_ids = {n.id for n in graph.nodes}

    assert a.id not in node_ids  # old app/a.py node gone
    assert a2.id in node_ids  # replaced by the re-parsed one
    assert b.id in node_ids  # untouched file's node survives
    assert c.id in node_ids  # untouched file's node survives
    # The edge from the deleted A node is gone with it (DETACH DELETE) —
    # not silently left dangling.
    assert graph.edges == []


async def test_scoped_update_never_touches_nodes_without_a_file_path(
    repository_id: str, graph_repository: Neo4jGraphRepository
) -> None:
    """Project-level facts (dependencies, shared Kafka topics) carry no
    `file_path` at all (see app.indexer.graph.builder) — a scoped delete
    must never match them regardless of what `file_paths` contains."""
    dependency = GraphNode(
        id=f"{repository_id}:dependency:org:lib",
        labels=["MavenDependency"],
        properties={"group_id": "org", "artifact_id": "lib"},
    )
    a = _component(repository_id, "A", "app/a.py")
    await graph_repository.replace_repository_graph(
        repository_id, GraphPayload(nodes=[dependency, a], edges=[])
    )

    await graph_repository.replace_repository_files_subgraph(
        repository_id, ["app/a.py"], GraphPayload(nodes=[], edges=[])
    )

    graph = await graph_repository.get_full_graph(repository_id)
    node_ids = {n.id for n in graph.nodes}
    assert dependency.id in node_ids  # never had a file_path — never in scope
    assert a.id not in node_ids  # was in scope, and the update wrote nothing back


async def test_scoped_update_upserts_a_shared_node_without_deleting_it(
    repository_id: str, graph_repository: Neo4jGraphRepository
) -> None:
    """A changed file that still produces to the same Kafka topic: the
    topic node must be updated (MERGE), not deleted-then-orphaned, even
    though it's referenced by `graph`, not by `file_paths`."""
    topic = GraphNode(
        id=f"{repository_id}:kafka-topic:orders",
        labels=["KafkaTopic"],
        properties={"topic": "orders"},
    )
    a = _component(repository_id, "A", "app/a.py")
    produces = GraphEdge(source_id=a.id, target_id=topic.id, type="PRODUCES_TO")
    await graph_repository.replace_repository_graph(
        repository_id, GraphPayload(nodes=[topic, a], edges=[produces])
    )

    a2 = _component(repository_id, "A", "app/a.py")
    await graph_repository.replace_repository_files_subgraph(
        repository_id,
        ["app/a.py"],
        GraphPayload(nodes=[topic, a2], edges=[produces]),
    )

    graph = await graph_repository.get_full_graph(repository_id)
    node_ids = {n.id for n in graph.nodes}
    assert topic.id in node_ids
    assert a2.id in node_ids
    assert any(e.source_id == a2.id and e.target_id == topic.id for e in graph.edges)


async def test_empty_file_paths_is_a_delete_no_op_that_still_writes(
    repository_id: str, graph_repository: Neo4jGraphRepository
) -> None:
    a = _component(repository_id, "A", "app/a.py")
    await graph_repository.replace_repository_graph(repository_id, GraphPayload(nodes=[a]))

    b = _component(repository_id, "B", "app/b.py")
    await graph_repository.replace_repository_files_subgraph(
        repository_id, [], GraphPayload(nodes=[b])
    )

    graph = await graph_repository.get_full_graph(repository_id)
    node_ids = {n.id for n in graph.nodes}
    assert a.id in node_ids  # nothing was in the (empty) deletion scope
    assert b.id in node_ids  # still upserted
