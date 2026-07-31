"""Neo4j-backed implementation of `IGraphRepository`.

Cypher can't parameterize label names or relationship types, so both are
interpolated directly into query strings — safe here specifically because
they only ever come from the shared, internally-controlled allowlist in
`app.graph.neo4j_common` (also used by `Neo4jTestCaseGraphRepository`),
never from request input.
"""

from itertools import groupby
from typing import Any

from neo4j import AsyncDriver

from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_common import (
    _ALLOWED_LABELS,
    _BASE_LABEL,
    label_cypher,
    node_from_value,
    validate_labels,
    validate_rel_type,
)


class Neo4jGraphRepository(IGraphRepository):
    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver

    async def ensure_indexes(self) -> None:
        """Idempotent — safe to call on every app startup."""
        async with self._driver.session() as session:
            await session.run(
                f"CREATE INDEX graph_node_id IF NOT EXISTS FOR (n:`{_BASE_LABEL}`) ON (n.id)"
            )
            await session.run(
                f"CREATE INDEX graph_node_repository_id IF NOT EXISTS "
                f"FOR (n:`{_BASE_LABEL}`) ON (n.repository_id)"
            )

    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
        async with self._driver.session() as session, await session.begin_transaction() as tx:
            await tx.run(
                "MATCH (n {repository_id: $repository_id}) DETACH DELETE n",
                repository_id=repository_id,
            )
            await self._write_nodes(tx, repository_id, graph.nodes)
            await self._write_edges(tx, graph.edges)
            await tx.commit()

    async def _write_nodes(self, tx: Any, repository_id: str, nodes: list[GraphNode]) -> None:
        keyed = sorted(nodes, key=lambda n: validate_labels(n.labels))
        for labels, group in groupby(keyed, key=lambda n: validate_labels(n.labels)):
            payload = [
                {"id": node.id, "properties": {**node.properties, "repository_id": repository_id}}
                for node in group
            ]
            await tx.run(
                f"""
                UNWIND $nodes AS node
                MERGE (n{label_cypher(labels)} {{id: node.id}})
                SET n += node.properties
                """,
                nodes=payload,
            )

    async def _write_edges(self, tx: Any, edges: list[GraphEdge]) -> None:
        keyed = sorted(edges, key=lambda e: validate_rel_type(e.type))
        for rel_type, group in groupby(keyed, key=lambda e: validate_rel_type(e.type)):
            payload = [
                {
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "properties": edge.properties,
                }
                for edge in group
            ]
            await tx.run(
                f"""
                UNWIND $edges AS edge
                MATCH (a {{id: edge.source_id}}), (b {{id: edge.target_id}})
                MERGE (a)-[r:`{rel_type}`]->(b)
                SET r += edge.properties
                """,
                edges=payload,
            )

    async def get_full_graph(self, repository_id: str) -> GraphPayload:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n {repository_id: $repository_id})
                OPTIONAL MATCH (n)-[r]->(m {repository_id: $repository_id})
                RETURN n, r, m
                """,
                repository_id=repository_id,
            )
            records = [record async for record in result]

        nodes_by_id: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        for record in records:
            source = record["n"]
            nodes_by_id[source["id"]] = node_from_value(source)
            target = record["m"]
            relationship = record["r"]
            if target is not None:
                nodes_by_id[target["id"]] = node_from_value(target)
            if relationship is not None:
                edges.append(
                    GraphEdge(
                        source_id=relationship.start_node["id"],
                        target_id=relationship.end_node["id"],
                        type=relationship.type,
                        properties=dict(relationship),
                    )
                )

        return GraphPayload(nodes=list(nodes_by_id.values()), edges=edges)

    async def get_nodes_by_label(self, repository_id: str, label: str) -> list[GraphNode]:
        if label not in _ALLOWED_LABELS:
            raise ValueError(f"Unknown graph label: {label!r}")

        async with self._driver.session() as session:
            result = await session.run(
                f"MATCH (n:`{label}` {{repository_id: $repository_id}}) RETURN n",
                repository_id=repository_id,
            )
            records = [record async for record in result]

        return [node_from_value(record["n"]) for record in records]

    async def has_graph(self, repository_id: str) -> bool:
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (n {repository_id: $repository_id}) RETURN count(n) > 0 AS has_nodes",
                repository_id=repository_id,
            )
            record = await result.single()
            return bool(record["has_nodes"]) if record else False
