"""ADR 0018 RFC-06 — proves the Knowledge Materializer's core claim: delete
the Neo4j graph, replay Engineering Memory + its evidence packs, and the
regenerated graph matches the original — real git clone, real parse, real
Neo4j writes/reads, real Postgres persistence, no mocks.

`confidence` is deliberately excluded from the structural-equality
comparison and checked separately: it's a new, additive edge property
(see `materializer.py`'s module docstring) that today's direct-write
`graph/builder.py` never produced, so it cannot be part of an "identical
to before" comparison — instead this test proves it's present on every
materialized edge and matches Engineering Memory's current confidence for
that exact relationship.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.services.indexing_service import index_repository
from app.knowledge_engine.materializer import rematerialize_repository_graph
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def repository_row(db_session: AsyncSession) -> Repository:
    user = User(email=f"test-{uuid.uuid4()}@example.com", full_name="Test User")
    db_session.add(user)
    await db_session.flush()

    repo = Repository(
        user_id=user.id,
        owner="test-owner",
        name="spring-boot-repo",
        full_name="test-owner/spring-boot-repo",
        html_url="https://github.com/test-owner/spring-boot-repo",
        default_branch="main",
        source="github",
        github_repo_id=str(uuid.uuid4().int)[:10],
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


@pytest.fixture
async def graph_repository(
    repository_row: Repository,
) -> AsyncGenerator[Neo4jGraphRepository, None]:
    repo = Neo4jGraphRepository(get_driver())
    yield repo
    await repo.replace_repository_graph(str(repository_row.id), GraphPayload())


def _node_labels_and_properties(node: GraphNode) -> tuple[frozenset[str], str]:
    return frozenset(node.labels), json.dumps(node.properties, sort_keys=True, default=str)


def _edge_signature(edge: GraphEdge) -> tuple[str, str, str, str]:
    non_confidence_properties = {k: v for k, v in edge.properties.items() if k != "confidence"}
    return (
        edge.source_id,
        edge.type,
        edge.target_id,
        json.dumps(non_confidence_properties, sort_keys=True, default=str),
    )


async def test_replay_regenerates_an_equivalent_graph(
    spring_boot_git_repo: Path,
    repository_row: Repository,
    graph_repository: Neo4jGraphRepository,
    db_session: AsyncSession,
) -> None:
    repository_id = str(repository_row.id)

    await index_repository(
        repository_id=repository_id,
        html_url=str(spring_boot_git_repo),
        ref="main",
        db=db_session,
    )

    original = await graph_repository.get_full_graph(repository_id)
    assert original.nodes, "fixture repository produced no nodes — nothing to replay"
    assert original.edges, "fixture repository produced no edges — nothing to replay"

    # 1. Delete the graph.
    await graph_repository.replace_repository_graph(repository_id, GraphPayload())
    assert not await graph_repository.has_graph(repository_id)

    # 2 & 3. Replay Engineering Memory + evidence packs, regenerate Neo4j.
    await rematerialize_repository_graph(db_session, graph_repository, repository_row.id)

    # 4. Compare original vs regenerated.
    regenerated = await graph_repository.get_full_graph(repository_id)

    assert len(regenerated.nodes) == len(original.nodes)
    original_nodes_by_id = {n.id: n for n in original.nodes}
    regenerated_nodes_by_id = {n.id: n for n in regenerated.nodes}
    assert set(original_nodes_by_id) == set(regenerated_nodes_by_id)
    for node_id, original_node in original_nodes_by_id.items():
        assert _node_labels_and_properties(original_node) == _node_labels_and_properties(
            regenerated_nodes_by_id[node_id]
        ), f"node {node_id} differs after replay"

    assert len(regenerated.edges) == len(original.edges)
    original_signatures = sorted(_edge_signature(e) for e in original.edges)
    regenerated_signatures = sorted(_edge_signature(e) for e in regenerated.edges)
    assert original_signatures == regenerated_signatures

    memory = EngineeringMemoryService(db_session)
    current_relationships = await memory.get_current_relationships(repository_row.id)
    confidence_by_relationship = {
        (r.relationship_type, r.source_entity, r.target_entity): r.confidence_state
        for r in current_relationships
    }
    for edge in regenerated.edges:
        key = (edge.type, edge.source_id, edge.target_id)
        assert (
            edge.properties.get("confidence") == confidence_by_relationship[key]
        ), f"materialized confidence missing/mismatched for {key}"


async def test_materialize_repository_never_indexed_with_persistence_returns_empty(
    db_session: AsyncSession,
) -> None:
    from app.knowledge_engine.materializer import materialize_repository_graph

    never_indexed_repository_id = uuid.uuid4()

    payload = await materialize_repository_graph(db_session, never_indexed_repository_id)

    assert payload == GraphPayload()
