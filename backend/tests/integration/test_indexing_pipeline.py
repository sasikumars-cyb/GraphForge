"""`index_repository` end-to-end: real local git clone -> real tree-sitter
parse -> real Neo4j write/read. No mocks anywhere in this chain.
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.services.indexing_service import UnsupportedRepositoryError, index_repository

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repository_id() -> str:
    return f"test-pipeline-{uuid.uuid4()}"


@pytest.fixture
async def graph_repository(repository_id: str) -> AsyncGenerator[Neo4jGraphRepository, None]:
    repo = Neo4jGraphRepository(get_driver())
    yield repo
    await repo.replace_repository_graph(repository_id, GraphPayload())


async def test_indexes_spring_boot_repository_into_neo4j(
    spring_boot_git_repo: Path, repository_id: str, graph_repository: Neo4jGraphRepository
) -> None:
    summary = await index_repository(
        repository_id=repository_id, html_url=str(spring_boot_git_repo), ref="main"
    )

    assert summary == {
        "controllers": 1,
        "endpoints": 4,
        "services": 1,
        "feign_clients": 1,
        "kafka_producers": 2,
        "kafka_consumers": 2,
        "maven_dependencies": 4,
    }

    assert await graph_repository.has_graph(repository_id)

    graph = await graph_repository.get_full_graph(repository_id)
    node_label_sets = {tuple(sorted(node.labels)) for node in graph.nodes}
    assert ("Component", "Controller", "GraphNode") in node_label_sets
    assert ("Component", "FeignClient", "GraphNode") in node_label_sets
    assert ("GraphNode", "KafkaTopic") in node_label_sets

    edge_types = {edge.type for edge in graph.edges}
    assert {
        "CONTAINS",
        "EXPOSES",
        "CALLS",
        "PRODUCES_TO",
        "CONSUMES_FROM",
        "DEPENDS_ON",
    } <= edge_types


async def test_reindexing_replaces_the_previous_graph(
    spring_boot_git_repo: Path, repository_id: str, graph_repository: Neo4jGraphRepository
) -> None:
    await index_repository(
        repository_id=repository_id, html_url=str(spring_boot_git_repo), ref="main"
    )
    first_graph = await graph_repository.get_full_graph(repository_id)

    await index_repository(
        repository_id=repository_id, html_url=str(spring_boot_git_repo), ref="main"
    )
    second_graph = await graph_repository.get_full_graph(repository_id)

    assert len(first_graph.nodes) == len(second_graph.nodes)
    assert len(first_graph.edges) == len(second_graph.edges)


async def test_unsupported_repository_raises_and_writes_no_graph(
    unsupported_git_repo: Path, repository_id: str, graph_repository: Neo4jGraphRepository
) -> None:
    with pytest.raises(UnsupportedRepositoryError):
        await index_repository(
            repository_id=repository_id, html_url=str(unsupported_git_repo), ref="main"
        )

    assert not await graph_repository.has_graph(repository_id)
