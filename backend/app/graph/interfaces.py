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
