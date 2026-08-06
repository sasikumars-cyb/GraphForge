"""Unit tests for KAN-16's shadow-compare step - `materialize_repository_graph`
itself is mocked so these assert only the comparison/logging contract
(match vs. mismatch vs. materializer failure), independent of Postgres/
Neo4j. `test_materializer_replay.py` covers the real end-to-end pipeline
these signatures are meant to match."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.knowledge_engine.shadow_compare import shadow_compare_materialized_graph

pytestmark = pytest.mark.asyncio


def _graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> GraphPayload:
    return GraphPayload(nodes=nodes, edges=edges)


async def test_identical_graphs_report_a_match(db_session) -> None:
    node = GraphNode(id="n1", labels=["Service"], properties={"name": "billing"})
    edge = GraphEdge(source_id="n1", target_id="n2", type="CALLS", properties={})
    graph = _graph([node], [edge])

    with patch(
        "app.knowledge_engine.shadow_compare.materialize_repository_graph",
        new=AsyncMock(return_value=graph),
    ):
        result = await shadow_compare_materialized_graph(db_session, str(uuid.uuid4()), graph)

    assert result is True


async def test_graphs_differing_only_by_confidence_property_still_match(db_session) -> None:
    # `confidence` is additive-only (see materializer.py's module
    # docstring) - a materialized edge carrying it while the direct-write
    # edge doesn't must not be reported as a mismatch.
    direct_edge = GraphEdge(source_id="n1", target_id="n2", type="CALLS", properties={})
    materialized_edge = GraphEdge(
        source_id="n1", target_id="n2", type="CALLS", properties={"confidence": "high"}
    )
    node = GraphNode(id="n1", labels=["Service"], properties={})
    direct_graph = _graph([node], [direct_edge])
    materialized_graph = _graph([node], [materialized_edge])

    with patch(
        "app.knowledge_engine.shadow_compare.materialize_repository_graph",
        new=AsyncMock(return_value=materialized_graph),
    ):
        result = await shadow_compare_materialized_graph(
            db_session, str(uuid.uuid4()), direct_graph
        )

    assert result is True


async def test_a_node_missing_from_the_materialized_graph_is_a_mismatch(db_session) -> None:
    direct_graph = _graph(
        [
            GraphNode(id="n1", labels=["Service"], properties={}),
            GraphNode(id="n2", labels=["Service"], properties={}),
        ],
        [],
    )
    materialized_graph = _graph([GraphNode(id="n1", labels=["Service"], properties={})], [])

    with patch(
        "app.knowledge_engine.shadow_compare.materialize_repository_graph",
        new=AsyncMock(return_value=materialized_graph),
    ):
        result = await shadow_compare_materialized_graph(
            db_session, str(uuid.uuid4()), direct_graph
        )

    assert result is False


async def test_materializer_failure_is_swallowed_and_reported_as_unknown(db_session) -> None:
    graph = _graph([], [])

    with patch(
        "app.knowledge_engine.shadow_compare.materialize_repository_graph",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await shadow_compare_materialized_graph(db_session, str(uuid.uuid4()), graph)

    assert result is None
