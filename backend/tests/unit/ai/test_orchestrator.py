"""Unit tests for the Orchestrator: Registry, Selector, and RunCoordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents._contract import (
    AgentContext,
    AgentManifest,
    AgentOutput,
    Confidence,
    Evidence,
    Subject,
)
from app.orchestrator.registry import AgentRegistry
from app.orchestrator.selector import GOAL_PLAN_FREEFORM, GOAL_REVIEW_PR, AgentSelector


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------


def _make_manifest(agent_id: str, goal: str = "test_goal") -> AgentManifest:
    return AgentManifest(
        agent_id=agent_id,
        purpose=f"Test agent {agent_id}.",
        goals=frozenset({goal}),
        accepted_subject_types=frozenset({"freetext"}),
        cost_class="cheap",
    )


def test_registry_register_and_get() -> None:
    registry = AgentRegistry()
    manifest = _make_manifest("planning", "plan_freeform")
    agent = MagicMock()
    registry.register(manifest, agent)

    result = registry.get("planning")
    assert result is not None
    assert result[0].agent_id == "planning"
    assert result[1] is agent


def test_registry_duplicate_raises() -> None:
    registry = AgentRegistry()
    manifest = _make_manifest("planning", "plan_freeform")
    agent = MagicMock()
    registry.register(manifest, agent)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(manifest, agent)


def test_registry_get_nonexistent_returns_none() -> None:
    registry = AgentRegistry()
    assert registry.get("nonexistent") is None


def test_registry_all_manifests_ordered() -> None:
    registry = AgentRegistry()
    registry.register(_make_manifest("review", "review_pr"), MagicMock())
    registry.register(_make_manifest("planning", "plan_freeform"), MagicMock())
    manifests = registry.all_manifests()
    assert [m.agent_id for m in manifests] == ["planning", "review"]


# ---------------------------------------------------------------------------
# AgentSelector
# ---------------------------------------------------------------------------


def _selector_with_agents() -> AgentSelector:
    """Build a selector backed by a registry with both agents registered."""
    registry = AgentRegistry()
    registry.register(_make_manifest("review", "review_pr"), MagicMock())
    registry.register(_make_manifest("planning", "plan_freeform"), MagicMock())
    return AgentSelector(registry)


def test_selector_review_pr() -> None:
    selector = _selector_with_agents()
    assert selector.select(GOAL_REVIEW_PR) == "review"


def test_selector_plan_freeform() -> None:
    selector = _selector_with_agents()
    assert selector.select(GOAL_PLAN_FREEFORM) == "planning"


def test_selector_unknown_goal_raises() -> None:
    from app.core.exceptions import NotFoundError

    selector = _selector_with_agents()
    with pytest.raises(NotFoundError):
        selector.select("nonexistent_goal")


def test_selector_known_goals_returns_all() -> None:
    selector = _selector_with_agents()
    goals = selector.known_goals()
    assert GOAL_REVIEW_PR in goals
    assert GOAL_PLAN_FREEFORM in goals
