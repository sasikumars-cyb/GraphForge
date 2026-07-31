"""Contract and Neo4j implementation for the TestRail test-case graph.

Deliberately a separate small repository from `IGraphRepository`/
`Neo4jGraphRepository`, not an extension of it: that one's
`replace_repository_graph` deletes *every* node tagged with a given
`repository_id` property before rewriting (see its own docstring) — test
case nodes must never share that scoping property, or a code re-index
would silently wipe them (and vice versa, a test-case re-sync would wipe
the code graph). Scoped by `testrail_project_id` instead, using the exact
same delete-then-MERGE shape and the same shared label/relationship-type
allowlist (`app.graph.neo4j_common`) so both writers stay safe against
Cypher injection for the same reason.
"""

from abc import ABC, abstractmethod
from itertools import groupby
from typing import Any

from neo4j import AsyncDriver

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_common import (
    _BASE_LABEL,
    label_cypher,
    node_from_value,
    validate_labels,
    validate_rel_type,
)


class ITestCaseGraphRepository(ABC):
    """Port for persisting and querying the TestRail test-case graph."""

    @abstractmethod
    async def replace_project_test_cases(self, project_id: str, graph: GraphPayload) -> None:
        """Delete any existing test-case graph for `project_id` and write
        `graph` in its place — each sync fully replaces the prior one,
        same as `IGraphRepository.replace_repository_graph`."""
        raise NotImplementedError

    @abstractmethod
    async def get_project_test_cases(self, project_id: str) -> GraphPayload:
        """Return every node and edge synced for `project_id`."""
        raise NotImplementedError

    @abstractmethod
    async def get_all_test_cases(self, limit: int) -> list[GraphNode]:
        """Return up to `limit` TestCase nodes across every synced
        project — used by the Testing agent's coverage lookup
        (app.agents.testing.tools.TestRailCoverageTool), which reasons
        over test case titles by relevance, not by which project they
        came from (no code linkage in this pass — see this module's own
        docstring)."""
        raise NotImplementedError

    @abstractmethod
    async def has_synced(self, project_id: str) -> bool:
        """Whether `project_id` has ever been successfully synced."""
        raise NotImplementedError


class Neo4jTestCaseGraphRepository(ITestCaseGraphRepository):
    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver

    async def ensure_indexes(self) -> None:
        """Idempotent — safe to call on every app startup, mirrors
        Neo4jGraphRepository.ensure_indexes."""
        async with self._driver.session() as session:
            await session.run(
                f"CREATE INDEX graph_node_testrail_project_id IF NOT EXISTS "
                f"FOR (n:`{_BASE_LABEL}`) ON (n.testrail_project_id)"
            )

    async def replace_project_test_cases(self, project_id: str, graph: GraphPayload) -> None:
        async with self._driver.session() as session, await session.begin_transaction() as tx:
            await tx.run(
                "MATCH (n {testrail_project_id: $project_id}) DETACH DELETE n",
                project_id=project_id,
            )
            await self._write_nodes(tx, project_id, graph.nodes)
            await self._write_edges(tx, graph.edges)
            await tx.commit()

    async def _write_nodes(self, tx: Any, project_id: str, nodes: list[GraphNode]) -> None:
        keyed = sorted(nodes, key=lambda n: validate_labels(n.labels))
        for labels, group in groupby(keyed, key=lambda n: validate_labels(n.labels)):
            payload = [
                {
                    "id": node.id,
                    "properties": {**node.properties, "testrail_project_id": project_id},
                }
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

    async def get_project_test_cases(self, project_id: str) -> GraphPayload:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n {testrail_project_id: $project_id})
                OPTIONAL MATCH (n)-[r]->(m {testrail_project_id: $project_id})
                RETURN n, r, m
                """,
                project_id=project_id,
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

    async def get_all_test_cases(self, limit: int) -> list[GraphNode]:
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (n:`TestCase`) RETURN n LIMIT $limit",
                limit=limit,
            )
            records = [record async for record in result]
        return [node_from_value(record["n"]) for record in records]

    async def has_synced(self, project_id: str) -> bool:
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (n {testrail_project_id: $project_id}) RETURN count(n) > 0 AS has_nodes",
                project_id=project_id,
            )
            record = await result.single()
            return bool(record["has_nodes"]) if record else False
