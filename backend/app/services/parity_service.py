"""ADR 0018 — Graph Parity Engine orchestration: fetches the two
already-existing `GraphPayload` representations (legacy Neo4j graph via
`IGraphRepository.get_full_graph`, the Engineering Memory projection via
`app.knowledge_engine.materializer.materialize_repository_graph`) and
hands them to the unmodified `compare_graphs` comparator.

Read-only, by construction: `get_full_graph` and `materialize_repository_graph`
are both read paths (verified — neither calls any `replace_*`/write method
on `IGraphRepository`, and `materialize_repository_graph` — as opposed to
`rematerialize_repository_graph`, deliberately not called here — never
writes to Neo4j). This module writes nothing, to Neo4j or Postgres,
computes nothing the comparator doesn't already compute, and does not
implement Shadow Mode or Production Cutover — it only answers "what would
the comparator say about this repository right now."

`_strip_neo4j_readback_artifacts` exists because `get_full_graph` is a
*storage* read, not a pure re-serialization of what `build_graph` produced
— `app.graph.neo4j_common._write_nodes` stamps every node with a synthetic
`GraphNode` label (`_BASE_LABEL`) and injects `repository_id` into its
properties for MERGE/scoping purposes, and Neo4j itself surfaces the `id`
used as the MERGE key back as a real node property on read
(`node_from_value`: `properties=dict(value)` includes it). None of the
three ever existed in `build_graph`'s own `GraphNode.properties`, and none
of them exist in the Materializer's output either — they are Neo4j storage
mechanics, not a real graph-content difference, exactly the same class of
thing the comparator's `confidence`-vocabulary ignore rule already
accounts for at the edge level (found live, with a real indexed
repository, by this RFC's own integration test — not guessed). Fixed here,
in the service's read-adapter, specifically because the comparator itself
must stay unmodified per this RFC's explicit constraint — the comparator
only ever sees two already-comparable `GraphPayload`s.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphNode, GraphPayload
from app.knowledge_engine.materializer import materialize_repository_graph
from app.knowledge_engine.parity.comparator import compare_graphs
from app.knowledge_engine.parity.ignore_rules import DEFAULT_IGNORE_RULES, IgnoreRules
from app.knowledge_engine.parity.report import ParityReport

_NEO4J_BASE_LABEL = "GraphNode"
_NEO4J_READBACK_ONLY_PROPERTIES = frozenset({"id", "repository_id"})


def _strip_neo4j_readback_artifacts(payload: GraphPayload) -> GraphPayload:
    normalized_nodes = [
        GraphNode(
            id=node.id,
            labels=[label for label in node.labels if label != _NEO4J_BASE_LABEL],
            properties={
                key: value
                for key, value in node.properties.items()
                if key not in _NEO4J_READBACK_ONLY_PROPERTIES
            },
        )
        for node in payload.nodes
    ]
    return GraphPayload(nodes=normalized_nodes, edges=payload.edges)


async def run_parity_check(
    db: AsyncSession,
    graph_repository: IGraphRepository,
    repository_id: uuid.UUID,
    *,
    ignore_rules: IgnoreRules = DEFAULT_IGNORE_RULES,
) -> ParityReport:
    """Runs one parity comparison for `repository_id` against whatever is
    currently in Neo4j and Engineering Memory, right now. Not persisted —
    every call recomputes from live state, so the result is always fresh
    and there is no cache/staleness question to reason about."""
    legacy = _strip_neo4j_readback_artifacts(
        await graph_repository.get_full_graph(str(repository_id))
    )
    materialized = await materialize_repository_graph(db, repository_id)
    return compare_graphs(legacy, materialized, ignore_rules=ignore_rules)
