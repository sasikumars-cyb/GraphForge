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
        "python_modules": 0,
        "python_classes": 0,
        "python_functions": 0,
        "python_dependencies": 0,
        "sql_files": 0,
        "sql_table_references": 0,
        "config_files": 0,
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


async def test_indexes_python_repository_into_neo4j(
    python_git_repo: Path, repository_id: str, graph_repository: Neo4jGraphRepository
) -> None:
    summary = await index_repository(
        repository_id=repository_id, html_url=str(python_git_repo), ref="main"
    )

    assert summary["python_modules"] > 0
    assert summary["python_classes"] > 0
    assert summary["python_functions"] > 0
    assert summary["python_dependencies"] == 2

    assert await graph_repository.has_graph(repository_id)

    graph = await graph_repository.get_full_graph(repository_id)
    node_label_sets = {tuple(sorted(node.labels)) for node in graph.nodes}
    assert ("Component", "GraphNode", "Module") in node_label_sets
    assert ("Class", "Component", "GraphNode") in node_label_sets
    assert ("Component", "Function", "GraphNode") in node_label_sets

    edge_types = {edge.type for edge in graph.edges}
    assert {"CONTAINS", "IMPORTS", "INHERITS_FROM", "CALLS", "DEPENDS_ON"} <= edge_types


async def test_sql_lineage_end_to_end_function_reads_and_writes_data_tables(
    python_spark_git_repo: Path, repository_id: str, graph_repository: Neo4jGraphRepository
) -> None:
    """The proof the feature request asked for: a real `index_repository`
    run against a real (small) Spark/Databricks-shaped repository produces
    the full chain

        Python Function --READS_FROM--> Source DataTable
        Python Function --WRITES_TO--> Target DataTable

    from `pipeline/ingest.py`'s single `spark.sql("INSERT INTO ... SELECT
    ... FROM ...")` call - plus, separately, the `.sql`-file chain

        Python Module --LOADS_SQL--> SqlFile --READS_FROM--> DataTable

    from `pipeline/config/sql_registry.py`'s literal `SQL_FILE_MAP` naming
    `pipeline/sql/customers.sql`.
    """
    summary = await index_repository(
        repository_id=repository_id, html_url=str(python_spark_git_repo), ref="main"
    )
    assert summary["sql_files"] == 1
    assert summary["sql_table_references"] == 1

    graph = await graph_repository.get_full_graph(repository_id)
    edges = {(e.source_id, e.target_id, e.type) for e in graph.edges}
    node_ids = {n.id for n in graph.nodes}

    function_id = f"{repository_id}:function:pipeline.ingest.run_ingest"
    bronze_id = f"{repository_id}:data-table:catalog.schema.customer_bronze"
    gold_id = f"{repository_id}:data-table:catalog.schema.customer_gold"
    assert (function_id, bronze_id, "READS_FROM") in edges
    assert (function_id, gold_id, "WRITES_TO") in edges

    sql_file_id = f"{repository_id}:sql-file:pipeline/sql/customers.sql"
    raw_id = f"{repository_id}:data-table:catalog.schema.customer_raw"
    registry_module_id = f"{repository_id}:module:pipeline.config.sql_registry"
    assert sql_file_id in node_ids
    assert (sql_file_id, raw_id, "READS_FROM") in edges
    assert (registry_module_id, sql_file_id, "LOADS_SQL") in edges


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
