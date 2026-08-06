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

# The subset of the shared _ALLOWED_REL_TYPES (app.graph.neo4j_common) that
# crosses repository boundaries - `replace_cross_repository_edges`'s delete
# is scoped to exactly these so it can never touch the per-repository edges
# `replace_repository_graph` owns.
_CROSS_REPO_REL_TYPES = frozenset({"CALLS_SERVICE", "SHARES_TOPIC", "DEPENDS_ON_REPOSITORY"})


def _repository_node_id(repository_id: str) -> str:
    return f"{repository_id}:repository"


# Cypher cannot parameterize the hop-count bound of a variable-length
# relationship pattern (`*1..$n` is not valid Cypher — only a literal
# integer works there), so `get_neighborhood` interpolates `max_hops`
# directly into the query string, the same way labels/rel-types are
# interpolated above. Bounded here, before interpolation, so a caller
# passing an unreasonable value can't turn one call into an unbounded
# (or malformed) traversal - 5 hops is already generous for "components
# near an anchor" and far more than any caller in this codebase requests.
_MAX_NEIGHBORHOOD_HOPS = 5

# Hard ceiling on `get_full_graph`'s `limit`, enforced regardless of what a
# caller requests — same defense-in-depth reasoning as
# `_MAX_NEIGHBORHOOD_HOPS` above. High enough that no real "give me this
# repository's package-level graph" request should ever hit it; low enough
# that hitting it caps a single response well short of the point a browser
# tab would actually struggle to render it.
_MAX_FULL_GRAPH_LIMIT = 10_000


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

    async def get_full_graph(
        self,
        repository_id: str,
        *,
        limit: int | None = None,
        node_types: list[str] | None = None,
    ) -> GraphPayload:
        """The `OPTIONAL MATCH`'s target used to require `m.repository_id =
        $repository_id`, which structurally excludes every cross-repository
        edge (`replace_cross_repository_edges`'s CALLS_SERVICE/SHARES_TOPIC/
        DEPENDS_ON_REPOSITORY) from this read, even though they genuinely
        exist in Neo4j — `m`'s `repository_id` is always the *other*
        repository's, by definition of a cross-repository edge. The `WHERE`
        clause's `OR type(r) IN [...]` lets those edges through too.

        `m` itself (the other repository's own Repository node) is
        deliberately not added to the returned node set even when matched
        this way — only its id is needed for the edge's `target_id`, and
        `materialize_repository_graph`'s cross-repository edges are, by the
        same RFC-06 design (see its own docstring), edges-only: it never
        materializes a *foreign* Repository node either. Including it here
        would manufacture a node-side parity mismatch, not close one.

        `limit`/`node_types` (see the interface docstring) switch to a
        bounded, two-pass query — select the (capped, optionally
        label-filtered) node set first, then fetch edges among only that
        set — the same shape `get_neighborhood` below already uses, so a
        repository with a huge edge fan-out can never inflate the
        node-selection query, and an excluded type is never read off disk.
        `limit=None, node_types=None` (the default) is the original,
        unbounded query, byte-for-byte — every existing caller that passes
        neither keeps today's exact behavior.
        """
        if node_types is not None:
            unknown = set(node_types) - _ALLOWED_LABELS
            if unknown:
                raise ValueError(f"Unknown node type(s): {sorted(unknown)}")

        if limit is None and node_types is None:
            return await self._get_full_graph_unbounded(repository_id)
        return await self._get_full_graph_bounded(
            repository_id,
            limit=min(limit, _MAX_FULL_GRAPH_LIMIT) if limit is not None else _MAX_FULL_GRAPH_LIMIT,
            node_types=node_types,
        )

    async def _get_full_graph_unbounded(self, repository_id: str) -> GraphPayload:
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                MATCH (n {{repository_id: $repository_id}})
                OPTIONAL MATCH (n)-[r]->(m)
                WHERE m.repository_id = $repository_id OR type(r) IN {list(_CROSS_REPO_REL_TYPES)!r}
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
            if target is not None and target.get("repository_id") == repository_id:
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

    async def _get_full_graph_bounded(
        self, repository_id: str, *, limit: int, node_types: list[str] | None
    ) -> GraphPayload:
        label_filter = "AND any(l IN labels(n) WHERE l IN $node_types)" if node_types else ""

        async with self._driver.session() as session:
            # Pass 1: the node set itself — capped and (optionally)
            # label-filtered inside Cypher, not after fetching everything.
            # Ordered by id for a deterministic, stable-across-requests
            # selection (not true keyset pagination, but consistent enough
            # that re-running the same request returns the same page).
            node_result = await session.run(
                f"""
                MATCH (n {{repository_id: $repository_id}})
                WHERE true {label_filter}
                RETURN n
                ORDER BY n.id
                LIMIT $limit
                """,
                repository_id=repository_id,
                node_types=node_types,
                limit=limit,
            )
            node_records = [record async for record in node_result]
            nodes_by_id = {rec["n"]["id"]: node_from_value(rec["n"]) for rec in node_records}
            node_ids = list(nodes_by_id.keys())

            # Only pay for a count() when the node query actually filled the
            # page — fewer results than `limit` already proves nothing was
            # cut off, no second query needed to know that.
            truncated = False
            total_node_count = len(node_ids)
            if len(node_ids) == limit:
                count_result = await session.run(
                    f"""
                    MATCH (n {{repository_id: $repository_id}})
                    WHERE true {label_filter}
                    RETURN count(n) AS total
                    """,
                    repository_id=repository_id,
                    node_types=node_types,
                )
                count_record = await count_result.single()
                total_node_count = int(count_record["total"]) if count_record else len(node_ids)
                truncated = total_node_count > len(node_ids)

            if not node_ids:
                return GraphPayload(truncated=truncated, total_node_count=total_node_count)

            # Pass 2: edges among only the selected node set — mirrors
            # `get_neighborhood`'s own two-pass shape below. An edge to a
            # same-repository node that didn't make the cut is dropped
            # (there's nothing on screen for it to point at); a
            # cross-repository edge is kept regardless, same as the
            # unbounded query above.
            # `n.id`/`m.id` returned directly rather than via
            # `relationship.start_node`/`end_node` — the driver only
            # hydrates a relationship's endpoint node *properties* (as
            # opposed to just its internal element id) when that node is
            # also explicitly returned by the query, which `n`/`m` aren't
            # here (only `r` is). Matches `get_neighborhood`'s own
            # `RETURN x.id AS source_id, y.id AS target_id, ...` shape
            # below for exactly this reason.
            edge_result = await session.run(
                f"""
                UNWIND $node_ids AS nid
                MATCH (n {{id: nid, repository_id: $repository_id}})
                OPTIONAL MATCH (n)-[r]->(m)
                WHERE (m.repository_id = $repository_id AND m.id IN $node_ids)
                      OR type(r) IN {list(_CROSS_REPO_REL_TYPES)!r}
                RETURN n.id AS source_id, m.id AS target_id, type(r) AS rel_type,
                       properties(r) AS props
                """,
                node_ids=node_ids,
                repository_id=repository_id,
            )
            edges = [
                GraphEdge(
                    source_id=rec["source_id"],
                    target_id=rec["target_id"],
                    type=rec["rel_type"],
                    properties=dict(rec["props"]),
                )
                for rec in [row async for row in edge_result]
                if rec["rel_type"] is not None
            ]

        return GraphPayload(
            nodes=list(nodes_by_id.values()),
            edges=edges,
            truncated=truncated,
            total_node_count=total_node_count,
        )

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
        rel_pattern = "|".join(f"`{validate_rel_type(t)}`" for t in dict.fromkeys(edge_types))

        async with self._driver.session() as session:
            # Pass 1: every node within max_hops of any seed, via the
            # allowed edge types, either direction — undirected traversal
            # is deliberate here (a caller of X is exactly as relevant to
            # X's neighborhood as something X calls; direction is
            # preserved per-edge in pass 2 below for anyone who needs it).
            # `hop_distance` is the minimum over every seed and every path,
            # so a node reachable both at 1 hop from one seed and 3 hops
            # from another reports 1.
            # `b` is intentionally not filtered to `$repository_id` — a
            # cross-repository edge type in `edge_types` (CALLS_SERVICE/
            # SHARES_TOPIC/DEPENDS_ON_REPOSITORY) only ever connects two
            # Repository nodes across different repositories, by
            # construction of `cross_repo_linker.py`; every other edge
            # type in `rel_pattern` only ever connects nodes already
            # sharing one `repository_id` (each repository's own graph is
            # written in one `replace_repository_graph` call). So the edge
            # *type* is already what enforces the boundary — filtering `b`
            # by `$repository_id` on top of that only blocked the
            # cross-repository case, it added no real safety for the
            # single-repository one.
            neighbor_result = await session.run(
                f"""
                MATCH (a:`{_BASE_LABEL}` {{repository_id: $repository_id}})
                WHERE a.id IN $seed_ids
                MATCH p = (a)-[:{rel_pattern}*1..{max_hops}]-(b:`{_BASE_LABEL}`)
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
            # Same reasoning as pass 1: `x`/`y` are scoped by `$all_ids`
            # (exactly the nodes pass 1 already found reachable), not by a
            # `repository_id` property — a cross-repository edge's two
            # endpoints never share one, by definition.
            all_ids = list(nodes_by_id.keys())
            edge_result = await session.run(
                f"""
                MATCH (x:`{_BASE_LABEL}`)-[r:{rel_pattern}]->(y:`{_BASE_LABEL}`)
                WHERE x.id IN $all_ids AND y.id IN $all_ids
                RETURN x.id AS source_id, y.id AS target_id, type(r) AS rel_type,
                       properties(r) AS props
                """,
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
