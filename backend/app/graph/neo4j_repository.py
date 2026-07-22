"""Neo4j-backed implementation of `IGraphRepository`.

Cypher can't parameterize label names or relationship types, so both are
interpolated directly into query strings — safe here specifically because
they only ever come from `_ALLOWED_LABELS`/`_ALLOWED_REL_TYPES` below
(a fixed, internally-controlled vocabulary), never from request input.
"""

from itertools import groupby
from typing import Any

from neo4j import AsyncDriver

from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload

# Every node/relationship type this codebase ever writes. Extend when the
# indexer's vocabulary grows (see app.indexer.graph.builder) - anything not
# listed here is refused, not silently interpolated.
_ALLOWED_LABELS = frozenset(
    {
        "GraphNode",
        "Repository",
        "Component",
        "Controller",
        "Service",
        "FeignClient",
        "Endpoint",
        "KafkaTopic",
        "MavenDependency",
    }
)
_ALLOWED_REL_TYPES = frozenset(
    {
        "CONTAINS",
        "EXPOSES",
        "CALLS",
        "PRODUCES_TO",
        "CONSUMES_FROM",
        "DEPENDS_ON",
    }
)

# Base label every node gets, regardless of its semantic labels - lets a
# single index cover `id`/`repository_id` lookups for every node type.
_BASE_LABEL = "GraphNode"


def _validate_labels(labels: list[str]) -> tuple[str, ...]:
    unknown = set(labels) - _ALLOWED_LABELS
    if unknown:
        raise ValueError(f"Refusing to write unknown graph label(s): {sorted(unknown)}")
    ordered = [_BASE_LABEL, *sorted(label for label in labels if label != _BASE_LABEL)]
    # dict.fromkeys: de-dupe while preserving order (a node may already list _BASE_LABEL).
    return tuple(dict.fromkeys(ordered))


def _validate_rel_type(rel_type: str) -> str:
    if rel_type not in _ALLOWED_REL_TYPES:
        raise ValueError(f"Refusing to write unknown relationship type: {rel_type!r}")
    return rel_type


def _label_cypher(labels: tuple[str, ...]) -> str:
    return "".join(f":`{label}`" for label in labels)


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
        keyed = sorted(nodes, key=lambda n: _validate_labels(n.labels))
        for labels, group in groupby(keyed, key=lambda n: _validate_labels(n.labels)):
            payload = [
                {"id": node.id, "properties": {**node.properties, "repository_id": repository_id}}
                for node in group
            ]
            await tx.run(
                f"""
                UNWIND $nodes AS node
                MERGE (n{_label_cypher(labels)} {{id: node.id}})
                SET n += node.properties
                """,
                nodes=payload,
            )

    async def _write_edges(self, tx: Any, edges: list[GraphEdge]) -> None:
        keyed = sorted(edges, key=lambda e: _validate_rel_type(e.type))
        for rel_type, group in groupby(keyed, key=lambda e: _validate_rel_type(e.type)):
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
            nodes_by_id[source["id"]] = GraphNode(
                id=source["id"], labels=list(source.labels), properties=dict(source)
            )
            target = record["m"]
            relationship = record["r"]
            if target is not None:
                nodes_by_id[target["id"]] = GraphNode(
                    id=target["id"], labels=list(target.labels), properties=dict(target)
                )
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

        return [
            GraphNode(
                id=record["n"]["id"], labels=list(record["n"].labels), properties=dict(record["n"])
            )
            for record in records
        ]

    async def has_graph(self, repository_id: str) -> bool:
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (n {repository_id: $repository_id}) RETURN count(n) > 0 AS has_nodes",
                repository_id=repository_id,
            )
            record = await result.single()
            return bool(record["has_nodes"]) if record else False
