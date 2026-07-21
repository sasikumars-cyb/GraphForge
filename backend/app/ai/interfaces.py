"""Contract for the AI engine that reasons about a proposed change.

Implemented by nothing yet. Kept behind this interface so that whichever
service calls it never depends on which model or provider is behind it.
"""

from abc import ABC, abstractmethod
from typing import Any


class IAnalysisEngine(ABC):
    """Port for AI-driven impact analysis of a proposed change."""

    @abstractmethod
    async def analyze_change(self, diff: Any, graph: Any) -> Any:
        """Return an impact analysis for `diff` against `graph`."""
        raise NotImplementedError
