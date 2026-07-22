"""Contract for the read-only graph traversals impact analysis needs.

Kept separate from `app.graph.IGraphRepository` (the generic graph
read/write port) since these queries are specific to *how* impact
propagates through the architecture graph, not generic graph storage.
`Neo4jImpactGraphReader` is the one real implementation; a future
non-Neo4j graph store would only mean a new class here.
"""

from abc import ABC, abstractmethod

from app.analysis.graph.models import TraversalHop
from app.graph.models import GraphNode


class IImpactGraphReader(ABC):
    @abstractmethod
    async def find_nodes_by_file_paths(
        self, repository_id: str, file_paths: set[str]
    ) -> list[GraphNode]:
        """Nodes in `repository_id`'s graph whose `file_path` property is
        in `file_paths` - the "map changed files to indexed graph nodes"
        step."""
        raise NotImplementedError

    @abstractmethod
    async def find_downstream_apis(
        self, repository_id: str, node_ids: set[str]
    ) -> list[TraversalHop]:
        """`Endpoint` nodes reached from `node_ids` via `EXPOSES` (a
        Controller's own endpoints) or `CALLS` (a Feign client's declared
        remote calls)."""
        raise NotImplementedError

    @abstractmethod
    async def find_downstream_topics(
        self, repository_id: str, node_ids: set[str]
    ) -> list[TraversalHop]:
        """`KafkaTopic` nodes reached from `node_ids` via `PRODUCES_TO` or
        `CONSUMES_FROM`."""
        raise NotImplementedError

    @abstractmethod
    async def find_same_repository_topic_peers(
        self, repository_id: str, topic_ids: set[str], exclude_node_ids: set[str]
    ) -> list[TraversalHop]:
        """Other components in the *same* repository that also produce to
        or consume from any of `topic_ids` - the same-service side of
        Kafka pub/sub coupling."""
        raise NotImplementedError

    @abstractmethod
    async def find_cross_repository_topic_peers(
        self, topic_names: set[str], exclude_repository_id: str
    ) -> list[TraversalHop]:
        """Components in *other* indexed repositories whose `KafkaTopic`
        nodes share a `name` in `topic_names` - the cross-service side of
        Kafka pub/sub coupling (see ADR 0008 for why topic-name matching,
        not a graph edge, is how this is discovered: no edge crosses a
        repository boundary in this graph)."""
        raise NotImplementedError

    @abstractmethod
    async def get_dependencies(self, repository_id: str) -> list[GraphNode]:
        """Every `MavenDependency` node declared by `repository_id`."""
        raise NotImplementedError
