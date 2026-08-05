"""ADR 0018 RFC-06 follow-on (KAN-16) — shadow-compares the Materializer's
projection against the graph `index_repository` just built and wrote
directly, on every real indexing run that persists to Engineering Memory.

This is the "shadow-write/compare before full cutover" step KAN-16's own
risk note calls for: before the live write path (`replace_repository_graph`
in `indexing_service.py`) is cut over to go exclusively through the
Materializer, this builds real production evidence that the two paths
already agree - across every repository actually indexed, not just the
one fixture the replay test (`test_materializer_replay.py`) exercises.

Diagnostic only, same contract as `run_shadow_hypothesis_generation`:
never raises, never affects what was already committed to Neo4j or
`index_repository`'s return value. Call only after shadow hypothesis
generation has already run and persisted for this same commit, so the
Materializer has Engineering Memory evidence to read - see
`index_repository`'s call site.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.knowledge_engine.materializer import materialize_repository_graph

logger = logging.getLogger(__name__)


def _node_signature(node: GraphNode) -> tuple[str, frozenset[str], str]:
    properties = json.dumps(node.properties, sort_keys=True, default=str)
    return (node.id, frozenset(node.labels), properties)


def _edge_signature(edge: GraphEdge) -> tuple[str, str, str, str]:
    # `confidence` is deliberately excluded, same reasoning as the replay
    # test's own `_edge_signature`: it's a new, additive property the
    # direct-write path never produced, so it isn't part of an "do these
    # two paths agree" comparison.
    non_confidence_properties = {k: v for k, v in edge.properties.items() if k != "confidence"}
    return (
        edge.source_id,
        edge.type,
        edge.target_id,
        json.dumps(non_confidence_properties, sort_keys=True, default=str),
    )


async def shadow_compare_materialized_graph(
    db: AsyncSession, repository_id: str, directly_written_graph: GraphPayload
) -> bool | None:
    """Compares `directly_written_graph` (what `replace_repository_graph`
    was just called with) against what the Materializer would produce for
    the same repository right now, logging a structured match/mismatch
    event. Returns `True`/`False` for tests to assert against; callers in
    production code should not branch on the return value - this exists to
    produce a log signal, not to gate behavior.
    """
    try:
        materialized = await materialize_repository_graph(db, uuid.UUID(repository_id))
    except Exception:
        logger.exception("materializer_shadow_compare_failed repository_id=%s", repository_id)
        return None

    original_node_sigs = {_node_signature(n) for n in directly_written_graph.nodes}
    materialized_node_sigs = {_node_signature(n) for n in materialized.nodes}
    original_edge_sigs = {_edge_signature(e) for e in directly_written_graph.edges}
    materialized_edge_sigs = {_edge_signature(e) for e in materialized.edges}

    matches = (
        original_node_sigs == materialized_node_sigs
        and original_edge_sigs == materialized_edge_sigs
    )

    if matches:
        logger.info(
            "materializer_shadow_compare_match repository_id=%s node_count=%d edge_count=%d",
            repository_id,
            len(directly_written_graph.nodes),
            len(directly_written_graph.edges),
        )
        return True

    logger.warning(
        "materializer_shadow_compare_mismatch repository_id=%s "
        "nodes_only_in_direct_write=%d nodes_only_in_materialized=%d "
        "edges_only_in_direct_write=%d edges_only_in_materialized=%d",
        repository_id,
        len(original_node_sigs - materialized_node_sigs),
        len(materialized_node_sigs - original_node_sigs),
        len(original_edge_sigs - materialized_edge_sigs),
        len(materialized_edge_sigs - original_edge_sigs),
    )
    return False
