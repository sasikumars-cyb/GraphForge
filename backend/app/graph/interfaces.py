"""Contract for reading and writing the architecture graph.

`Neo4jGraphRepository` (neo4j_repository.py) is a real, working
implementation. The interface stays graph-store-agnostic on purpose - the
indexer produces a `GraphPayload` (app.graph.models) and never talks to
Neo4j directly, so a future non-Neo4j backend would only mean a new class
here, no change to the indexer or the API routers.
"""

from abc import ABC, abstractmethod

from app.graph.models import GraphEdge, GraphNode, GraphPayload


class IGraphRepository(ABC):
    """Port for persisting and querying the architecture graph."""

    @abstractmethod
    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
        """Delete any existing graph for `repository_id` and write `graph`
        in its place - each indexing run fully replaces the prior one
        rather than diffing against it."""
        raise NotImplementedError

    @abstractmethod
    async def get_full_graph(self, repository_id: str) -> GraphPayload:
        """Return every node and edge belonging to `repository_id`."""
        raise NotImplementedError

    @abstractmethod
    async def get_nodes_by_label(self, repository_id: str, label: str) -> list[GraphNode]:
        """Return nodes belonging to `repository_id` that carry `label`."""
        raise NotImplementedError

    @abstractmethod
    async def get_kafka_topic_edges(self, repository_id: str) -> list[GraphEdge]:
        """Every `PRODUCES_TO`/`CONSUMES_FROM` edge from a `repository_id`
        component to one of its own `KafkaTopic` nodes.

        A targeted alternative to `get_full_graph` for callers that only
        need Kafka producer/consumer direction (see
        `app.indexer.graph.cross_repo_linker`) - fetching every node and
        edge in the repository just to filter for these two relationship
        types is wasteful once a repository's graph is large.
        """
        raise NotImplementedError

    @abstractmethod
    async def has_graph(self, repository_id: str) -> bool:
        """Whether `repository_id` has ever been successfully indexed."""
        raise NotImplementedError

    @abstractmethod
    async def replace_cross_repository_edges(
        self, source_repository_id: str, edges: list[GraphEdge]
    ) -> None:
        """Delete every previously-computed cross-repository edge whose
        *source* is `source_repository_id`'s own Repository node, then write
        `edges` in its place.

        Scoped to `source_repository_id` specifically so re-linking one
        repository never touches edges another repository's own linking pass
        created — each repository owns exactly the edges it points *out*
        from. See `app.indexer.graph.cross_repo_linker`, the only writer.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_outgoing_cross_repository_edges(self, repository_id: str) -> list[GraphEdge]:
        """Every cross-repository edge whose source is `repository_id`'s own
        Repository node — what Context Discovery reads to turn a real graph
        relationship into a suggested repository."""
        raise NotImplementedError

    @abstractmethod
    async def get_neighborhood(
        self,
        repository_id: str,
        seed_node_ids: list[str],
        edge_types: list[str],
        max_hops: int,
    ) -> GraphPayload:
        """The induced subgraph within `max_hops` of `seed_node_ids`,
        traversing only `edge_types` (either direction).

        This is the hop-bounded traversal primitive `get_full_graph` is
        not: cost scales with the size of the neighborhood actually
        reachable from the seeds, never with the size of the whole
        repository — the difference that matters once a repository has
        thousands or millions of nodes (`get_full_graph` loads every one
        of them into memory regardless of how localized the caller's real
        interest is).

        Returns every node touched (seeds included) plus every edge of
        `edge_types` connecting them — an induced subgraph, not just a
        node list — so a caller can compute real connectivity-based
        ranking (BFS distance, personalized PageRank, ...) rather than
        only "was it reachable at all". Nodes carry a `hop_distance`
        property (minimum hops from ANY seed, 0 for the seeds
        themselves) that no other node in this graph legitimately has,
        since it's a query-scoped annotation, not a stored graph fact.

        An empty `seed_node_ids` or `edge_types` list returns an empty
        payload without querying — there is nothing to seed a
        neighborhood from, and asking anyway would either error or
        (worse) silently traverse every edge type.
        """
        raise NotImplementedError
