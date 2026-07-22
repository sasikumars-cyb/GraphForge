"""Shared helpers for turning a neo4j driver `Node` value into a
`GraphNode` - used by both `Neo4jGraphRepository` and
`app.analysis.graph.neo4j_impact_reader.Neo4jImpactGraphReader`, which
read from the same Neo4j database but for different purposes.
"""

from typing import Any

from app.graph.models import GraphNode


def node_from_value(value: Any) -> GraphNode:
    return GraphNode(id=value["id"], labels=list(value.labels), properties=dict(value))
