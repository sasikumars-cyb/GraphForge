"""Contract for reading and writing the dependency graph.

Implemented by nothing yet. Whatever implements this first (Postgres-backed
or Neo4j-backed) is an implementation detail services should not depend on
directly.
"""

from abc import ABC, abstractmethod
from typing import Any


class IGraphRepository(ABC):
    """Port for persisting and querying the dependency graph."""

    @abstractmethod
    async def get_graph(self, graph_id: str) -> Any:
        """Return the dependency graph identified by `graph_id`."""
        raise NotImplementedError
