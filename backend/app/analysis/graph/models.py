"""Shapes specific to impact-graph traversal - `app.graph.models.GraphNode`
still represents an individual node; `TraversalHop` adds the edge context
a traversal query returns."""

from dataclasses import dataclass

from app.graph.models import GraphNode


@dataclass(frozen=True)
class TraversalHop:
    """One edge encountered during traversal: `from_node -[relationship]->
    to_node`, exactly as directed in the graph (see `Neo4jImpactGraphReader`
    for what each traversal method's hop direction actually represents)."""

    from_node: GraphNode
    relationship: str
    to_node: GraphNode
