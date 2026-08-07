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
    # Set by `get_full_graph` when a `limit` cut the node set short — lets a
    # caller (the API response, then the frontend) distinguish "this is the
    # whole graph" from "this is the first `limit` nodes of a larger graph"
    # without re-counting anything itself. Every other producer of a
    # GraphPayload (get_neighborhood, get_kafka_topic_edges, ...) leaves
    # these at their defaults — they were never unbounded in the first
    # place, so there's nothing to report as truncated.
    truncated: bool = False
    total_node_count: int | None = None
    # ADR 0023 — the last node `id` in this page (nodes are always
    # returned `ORDER BY n.id`), to pass back as `after` for the next
    # page. `None` when this page wasn't truncated (nothing further to
    # fetch) or wasn't a bounded request at all — same "only meaningful
    # when truncated" convention `total_node_count` already has.
    next_cursor: str | None = None
