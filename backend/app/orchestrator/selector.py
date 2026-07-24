"""Agent Selector — maps a goal string to an agent_id.

Phase 1 is rule-based (if/elif on goal). An LLM-based selector is a
Phase 2/3 upgrade behind the same ISelector interface, so swapping it
never touches callers.

Goals are defined as constants here so callers import from one place
rather than scattering string literals across the codebase.

The actual goal→agent_id mapping is derived from the AgentRegistry's
manifests — each AgentManifest declares which goals it handles, so there
is exactly one source of truth (P1-1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import NotFoundError

if TYPE_CHECKING:
    from app.orchestrator.registry import AgentRegistry

# ---------------------------------------------------------------------------
# Goal constants — import these instead of using raw strings
# ---------------------------------------------------------------------------
GOAL_REVIEW_PR = "review_pr"
GOAL_PLAN_FREEFORM = "plan_freeform"
GOAL_DEVELOP_CHANGE_PLAN = "develop_change_plan"
GOAL_PLAN_TESTS = "plan_tests"
GOAL_REVIEW_READINESS = "review_readiness"


class AgentSelector:
    """Registry-backed selector. Returns agent_id for a given goal string.

    Derives the goal→agent_id mapping from the registered manifests so
    the mapping cannot diverge from what is actually registered.

    Raises NotFoundError for unrecognised goals (not a silent default) so
    callers fail fast rather than silently running the wrong agent.
    """

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def select(self, goal: str) -> str:
        for manifest in self._registry.all_manifests():
            if goal in manifest.goals:
                return manifest.agent_id
        known = sorted(g for m in self._registry.all_manifests() for g in m.goals)
        raise NotFoundError(f"No agent registered for goal '{goal}'. " f"Known goals: {known}")

    def known_goals(self) -> list[str]:
        return sorted(g for m in self._registry.all_manifests() for g in m.goals)
