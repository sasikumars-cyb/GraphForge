"""Generic graph shapes — the vocabulary `app.graph` and `app.indexer` share.

Deliberately domain-agnostic: `GraphNode`/`GraphEdge` know nothing about
"Controllers" or "Kafka topics" specifically. `app.indexer.graph.builder`
is what turns Java-specific discoveries into these generic shapes; this
module (and its Neo4j implementation) just stores and retrieves them.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GraphNode:
    """`id` must be globally unique (callers namespace it, e.g. by
    repository id) - it's the MERGE key in the Neo4j implementation."""

    id: str
    labels: list[str]
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    source_id: str
    target_id: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphPayload:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
