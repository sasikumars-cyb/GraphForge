"""Neo4j-backed implementation of `IImpactGraphReader`.

Every label and relationship type in these queries is a literal written
directly into the Cypher text (never interpolated from request input), so
none of the `_ALLOWED_LABELS`/`_ALLOWED_REL_TYPES` validation
`Neo4jGraphRepository` needs for its dynamic writes applies here - these
are fixed, read-only queries.
"""

from typing import Any

from neo4j import AsyncDriver

from app.analysis.graph.interfaces import IImpactGraphReader
from app.analysis.graph.models import TraversalHop
from app.graph.models import GraphNode
from app.graph.neo4j_common import node_from_value


class Neo4jImpactGraphReader(IImpactGraphReader):
    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver

    async def find_nodes_by_file_paths(
        self, repository_id: str, file_paths: set[str]
    ) -> list[GraphNode]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n {repository_id: $repository_id})
                WHERE n.file_path IN $file_paths
                RETURN n
                """,
                repository_id=repository_id,
                file_paths=list(file_paths),
            )
            records = [record async for record in result]
        return [node_from_value(record["n"]) for record in records]

    async def find_downstream_apis(
        self, repository_id: str, node_ids: set[str]
    ) -> list[TraversalHop]:
        return await self._traverse(
            """
            MATCH (start {repository_id: $repository_id})-[r:EXPOSES|CALLS]->(api:Endpoint)
            WHERE start.id IN $node_ids
            RETURN DISTINCT start, type(r) AS rel_type, api AS target
            """,
            repository_id=repository_id,
            node_ids=list(node_ids),
        )

    async def find_downstream_topics(
        self, repository_id: str, node_ids: set[str]
    ) -> list[TraversalHop]:
        return await self._traverse(
            """
            MATCH (start {repository_id: $repository_id})
                  -[r:PRODUCES_TO|CONSUMES_FROM]->(topic:KafkaTopic)
            WHERE start.id IN $node_ids
            RETURN DISTINCT start, type(r) AS rel_type, topic AS target
            """,
            repository_id=repository_id,
            node_ids=list(node_ids),
        )

    async def find_same_repository_topic_peers(
        self, repository_id: str, topic_ids: set[str], exclude_node_ids: set[str]
    ) -> list[TraversalHop]:
        return await self._traverse(
            """
            MATCH (peer:Component {repository_id: $repository_id})
                  -[r:PRODUCES_TO|CONSUMES_FROM]->(topic:KafkaTopic)
            WHERE topic.id IN $topic_ids AND NOT peer.id IN $exclude_node_ids
            RETURN DISTINCT peer AS start, type(r) AS rel_type, topic AS target
            """,
            repository_id=repository_id,
            topic_ids=list(topic_ids),
            exclude_node_ids=list(exclude_node_ids),
        )

    async def find_cross_repository_topic_peers(
        self, topic_names: set[str], exclude_repository_id: str
    ) -> list[TraversalHop]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (topic:KafkaTopic)
                WHERE topic.name IN $topic_names AND topic.repository_id <> $exclude_repository_id
                MATCH (peer:Component)-[r:PRODUCES_TO|CONSUMES_FROM]->(topic)
                RETURN DISTINCT peer AS start, type(r) AS rel_type, topic AS target
                """,
                topic_names=list(topic_names),
                exclude_repository_id=exclude_repository_id,
            )
            records = [record async for record in result]
        return [
            TraversalHop(
                from_node=node_from_value(record["start"]),
                relationship=record["rel_type"],
                to_node=node_from_value(record["target"]),
            )
            for record in records
        ]

    async def get_dependencies(self, repository_id: str) -> list[GraphNode]:
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (dep:MavenDependency {repository_id: $repository_id}) RETURN dep",
                repository_id=repository_id,
            )
            records = [record async for record in result]
        return [node_from_value(record["dep"]) for record in records]

    async def _traverse(self, query: str, **params: Any) -> list[TraversalHop]:
        async with self._driver.session() as session:
            result = await session.run(query, **params)
            records = [record async for record in result]
        return [
            TraversalHop(
                from_node=node_from_value(record["start"]),
                relationship=record["rel_type"],
                to_node=node_from_value(record["target"]),
            )
            for record in records
        ]
