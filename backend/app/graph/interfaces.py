"""Contract for reading and writing the architecture graph.

`Neo4jGraphRepository` (neo4j_repository.py) is a real, working
implementation. The interface stays graph-store-agnostic on purpose - the
indexer produces a `GraphPayload` (app.graph.models) and never talks to
Neo4j directly, so a future non-Neo4j backend would only mean a new class
here, no change to the indexer or the API routers.
"""

from abc import ABC, abstractmethod

from app.graph.models import GraphNode, GraphPayload


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
