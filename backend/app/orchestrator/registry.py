"""Agent Registry — maps agent_id to (AgentManifest, IAgent) pairs.

Dict-backed, populated at startup via register(). Every agent's manifest
module calls `global_registry.register(manifest, agent_instance)` once.

No dynamic plugin loading in Phase 1 (static code-defined registration).
"""

from __future__ import annotations

import logging

from app.agents._contract import AgentManifest, IAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """A simple dict-backed registry. Thread-safe for reads after startup
    (no concurrent writes once agents are registered)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[AgentManifest, IAgent]] = {}

    def register(self, manifest: AgentManifest, agent: IAgent) -> None:
        """Register an agent. Raises ValueError on duplicate agent_id."""
        if manifest.agent_id in self._store:
            raise ValueError(
                f"Agent '{manifest.agent_id}' is already registered. "
                "Each agent_id must be unique."
            )
        self._store[manifest.agent_id] = (manifest, agent)
        logger.info(
            "agent_registered agent_id=%s goals=%s cost_class=%s",
            manifest.agent_id,
            sorted(manifest.goals),
            manifest.cost_class,
        )

    def get(self, agent_id: str) -> tuple[AgentManifest, IAgent] | None:
        """Return (manifest, agent) for agent_id, or None if not found."""
        return self._store.get(agent_id)

    def all_manifests(self) -> list[AgentManifest]:
        """Return all registered manifests, ordered by agent_id."""
        return [manifest for manifest, _ in sorted(self._store.values(), key=lambda t: t[0].agent_id)]


# Module-level singleton — imported and used by agents and the RunCoordinator.
global_registry: AgentRegistry = AgentRegistry()
