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
from app.graph.neo4j_common import node_from_value

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
        "Module",
        "Class",
        "Function",
        "PythonDependency",
        "DataTable",
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
        "IMPORTS",
        "INHERITS_FROM",
        "READS_FROM",
        "WRITES_TO",
        # Cross-repository relationships — see
        # app.indexer.graph.cross_repo_linker, the only writer of these.
        # Unlike every other relationship above, both endpoints of these
        # three carry *different* `repository_id` values (each repository's
        # own `Repository` node) - the one deliberate exception to this
        # module's per-repository isolation.
        "CALLS_SERVICE",
        "SHARES_TOPIC",
        "DEPENDS_ON_REPOSITORY",
    }
)

# The subset of `_ALLOWED_REL_TYPES` that cross repository boundaries -
# `replace_cross_repository_edges`'s delete is scoped to exactly these so it
# can never touch the per-repository edges `replace_repository_graph` owns.
_CROSS_REPO_REL_TYPES = frozenset({"CALLS_SERVICE", "SHARES_TOPIC", "DEPENDS_ON_REPOSITORY"})


def _repository_node_id(repository_id: str) -> str:
    return f"{repository_id}:repository"

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


# Cypher cannot parameterize the hop-count bound of a variable-length
# relationship pattern (`*1..$n` is not valid Cypher — only a literal
# integer works there), so `get_neighborhood` interpolates `max_hops`
# directly into the query string, the same way labels/rel-types are
# interpolated above. Bounded here, before interpolation, so a caller
# passing an unreasonable value can't turn one call into an unbounded
# (or malformed) traversal - 5 hops is already generous for "components
# near an anchor" and far more than any caller in this codebase requests.
_MAX_NEIGHBORHOOD_HOPS = 5


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

    async def get_kafka_topic_edges(self, repository_id: str) -> list[GraphEdge]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (a {repository_id: $repository_id})
                      -[r:PRODUCES_TO|CONSUMES_FROM]->
                      (b:KafkaTopic {repository_id: $repository_id})
                RETURN a, r, b
                """,
                repository_id=repository_id,
            )
            records = [record async for record in result]

        return [
            GraphEdge(
                source_id=record["a"]["id"],
                target_id=record["b"]["id"],
                type=record["r"].type,
                properties=dict(record["r"]),
            )
            for record in records
        ]

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

    async def replace_cross_repository_edges(
        self, source_repository_id: str, edges: list[GraphEdge]
    ) -> None:
        source_node_id = _repository_node_id(source_repository_id)
        async with self._driver.session() as session, await session.begin_transaction() as tx:
            # Scoped on both the source node id AND the cross-repo rel-type
            # set, so this can never delete a same-repository edge that
            # happens to start at the Repository node (e.g. CONTAINS) nor
            # another repository's own outgoing cross-repo edges.
            await tx.run(
                f"""
                MATCH (a {{id: $source_node_id}})-[r]->(b)
                WHERE type(r) IN {list(_CROSS_REPO_REL_TYPES)!r}
                DELETE r
                """,
                source_node_id=source_node_id,
            )
            await self._write_edges(tx, edges)
            await tx.commit()

    async def get_outgoing_cross_repository_edges(self, repository_id: str) -> list[GraphEdge]:
        source_node_id = _repository_node_id(repository_id)
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                MATCH (a {{id: $source_node_id}})-[r]->(b)
                WHERE type(r) IN {list(_CROSS_REPO_REL_TYPES)!r}
                RETURN r, b
                """,
                source_node_id=source_node_id,
            )
            records = [record async for record in result]

        # `source_id` is always `source_node_id` here (every match starts at
        # it by construction) — read directly from the Python value rather
        # than `record["r"].start_node`, whose properties aren't hydrated
        # since `a` was never RETURNed (would otherwise silently yield
        # `None`; found via live verification against real Neo4j).
        return [
            GraphEdge(
                source_id=source_node_id,
                target_id=record["b"]["id"],
                type=record["r"].type,
                properties=dict(record["r"]),
            )
            for record in records
        ]

    async def get_neighborhood(
        self,
        repository_id: str,
        seed_node_ids: list[str],
        edge_types: list[str],
        max_hops: int,
    ) -> GraphPayload:
        if not seed_node_ids or not edge_types:
            return GraphPayload()
        if not isinstance(max_hops, int) or not (1 <= max_hops <= _MAX_NEIGHBORHOOD_HOPS):
            raise ValueError(
                f"max_hops must be an integer in [1, {_MAX_NEIGHBORHOOD_HOPS}], got {max_hops!r}."
            )
        rel_pattern = "|".join(f"`{_validate_rel_type(t)}`" for t in dict.fromkeys(edge_types))

        async with self._driver.session() as session:
            # Pass 1: every node within max_hops of any seed, via the
            # allowed edge types, either direction — undirected traversal
            # is deliberate here (a caller of X is exactly as relevant to
            # X's neighborhood as something X calls; direction is
            # preserved per-edge in pass 2 below for anyone who needs it).
            # `hop_distance` is the minimum over every seed and every path,
            # so a node reachable both at 1 hop from one seed and 3 hops
            # from another reports 1.
            neighbor_result = await session.run(
                f"""
                MATCH (a:`{_BASE_LABEL}` {{repository_id: $repository_id}})
                WHERE a.id IN $seed_ids
                MATCH p = (a)-[:{rel_pattern}*1..{max_hops}]-
                    (b:`{_BASE_LABEL}` {{repository_id: $repository_id}})
                WHERE NOT b.id IN $seed_ids
                WITH b, min(length(p)) AS hop_distance
                RETURN b, hop_distance
                """,
                repository_id=repository_id,
                seed_ids=seed_node_ids,
            )
            neighbor_records = [record async for record in neighbor_result]

            seed_result = await session.run(
                f"""
                MATCH (a:`{_BASE_LABEL}` {{repository_id: $repository_id}})
                WHERE a.id IN $seed_ids
                RETURN a
                """,
                repository_id=repository_id,
                seed_ids=seed_node_ids,
            )
            seed_records = [record async for record in seed_result]

            nodes_by_id: dict[str, GraphNode] = {}
            for record in seed_records:
                node = node_from_value(record["a"])
                nodes_by_id[node.id] = GraphNode(
                    id=node.id,
                    labels=node.labels,
                    properties={**node.properties, "hop_distance": 0},
                )
            for record in neighbor_records:
                node = node_from_value(record["b"])
                nodes_by_id[node.id] = GraphNode(
                    id=node.id,
                    labels=node.labels,
                    properties={**node.properties, "hop_distance": record["hop_distance"]},
                )

            if not nodes_by_id:
                return GraphPayload()

            # Pass 2: the induced subgraph — every edge of the allowed
            # types with BOTH endpoints among the nodes pass 1 found. This
            # is what lets a caller compute real connectivity (e.g.
            # personalized PageRank) over the neighborhood, not just a
            # flat "how far away" number.
            all_ids = list(nodes_by_id.keys())
            edge_result = await session.run(
                f"""
                MATCH (x:`{_BASE_LABEL}` {{repository_id: $repository_id}})
                      -[r:{rel_pattern}]->
                      (y:`{_BASE_LABEL}` {{repository_id: $repository_id}})
                WHERE x.id IN $all_ids AND y.id IN $all_ids
                RETURN x.id AS source_id, y.id AS target_id, type(r) AS rel_type,
                       properties(r) AS props
                """,
                repository_id=repository_id,
                all_ids=all_ids,
            )
            edge_records = [record async for record in edge_result]

        edges = [
            GraphEdge(
                source_id=record["source_id"],
                target_id=record["target_id"],
                type=record["rel_type"],
                properties=dict(record["props"]),
            )
            for record in edge_records
        ]
        return GraphPayload(nodes=list(nodes_by_id.values()), edges=edges)
