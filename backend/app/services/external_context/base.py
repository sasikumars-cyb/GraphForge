from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExternalContextItem:
    """Normalized external context item returned by a provider."""

    provider: str
    title: str
    summary: str
    details: str = ""
    references: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class ExternalContextProvider:
    """Base interface for external context connectors."""

    name: str = ""

    async def collect(self, user_input: str, config: dict[str, Any]) -> list[ExternalContextItem]:
        raise NotImplementedError
