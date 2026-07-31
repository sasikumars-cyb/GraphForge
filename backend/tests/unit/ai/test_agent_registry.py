"""Unit tests for AgentRegistry's runtime kill switch (disable/enable/
is_enabled) — see app.orchestrator.registry. Uses a fresh AgentRegistry()
instance throughout, never the process-level `global_registry` singleton,
so these can't leak state into any other test in the suite.
"""

from __future__ import annotations

import pytest

from app.agents._contract import AgentContext, AgentManifest, AgentOutput
from app.orchestrator.registry import AgentRegistry


class _FakeAgent:
    async def run(self, context: AgentContext) -> AgentOutput:  # pragma: no cover
        raise NotImplementedError


def _manifest(agent_id: str) -> AgentManifest:
    return AgentManifest(
        agent_id=agent_id,
        purpose=f"Test {agent_id}.",
        goals=frozenset({f"do_{agent_id}"}),
        accepted_subject_types=frozenset({"freetext"}),
        cost_class="cheap",
    )


def _registry_with(*agent_ids: str) -> AgentRegistry:
    registry = AgentRegistry()
    for agent_id in agent_ids:
        registry.register(_manifest(agent_id), _FakeAgent())
    return registry


def test_agents_are_enabled_by_default() -> None:
    registry = _registry_with("planning")
    assert registry.is_enabled("planning") is True


def test_disable_turns_is_enabled_false() -> None:
    registry = _registry_with("planning")

    registry.disable("planning")

    assert registry.is_enabled("planning") is False


def test_enable_reverses_a_disable() -> None:
    registry = _registry_with("planning")
    registry.disable("planning")

    registry.enable("planning")

    assert registry.is_enabled("planning") is True


def test_enable_on_an_already_enabled_agent_is_a_no_op() -> None:
    registry = _registry_with("planning")

    registry.enable("planning")  # never disabled

    assert registry.is_enabled("planning") is True


def test_disable_only_affects_the_named_agent() -> None:
    registry = _registry_with("planning", "testing")

    registry.disable("planning")

    assert registry.is_enabled("planning") is False
    assert registry.is_enabled("testing") is True


def test_unknown_agent_id_reads_as_enabled() -> None:
    """is_enabled() answers one question only ('was this turned off') -
    an unregistered agent_id was never disabled, so it reads True. Callers
    must check registry.get() separately to catch an unknown agent_id;
    RunCoordinator does exactly that, before it ever calls is_enabled()."""
    registry = AgentRegistry()

    assert registry.is_enabled("never-registered") is True


def test_get_returns_none_for_unregistered_agent() -> None:
    registry = _registry_with("planning")

    assert registry.get("never-registered") is None


def test_register_rejects_a_duplicate_agent_id() -> None:
    registry = _registry_with("planning")

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_manifest("planning"), _FakeAgent())


def test_all_manifests_is_ordered_by_agent_id() -> None:
    registry = _registry_with("testing", "planning", "development")

    ids = [m.agent_id for m in registry.all_manifests()]

    assert ids == ["development", "planning", "testing"]
