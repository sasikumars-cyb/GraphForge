"""Agent registration — called once at application startup.

Registers the Review Agent adapter and Planning Agent with the global
registry. Import this module in app/main.py to trigger registration.

Rule: only this file calls global_registry.register(). Agents never
self-register at module import time — that would create import-order
dependencies that are hard to reason about.
"""

from __future__ import annotations

import logging

from app.agents.planning.agent import PlanningAgent

logger = logging.getLogger(__name__)
from app.agents.planning.manifest import PLANNING_MANIFEST
from app.agents.review_adapter import REVIEW_MANIFEST, ReviewAgentAdapter
from app.orchestrator.registry import global_registry


def register_agents() -> None:
    """Register all agents with the global registry.

    Idempotent: safe to call multiple times (no-ops if already registered).
    In practice this is called exactly once from create_app().
    """
    # Check if already registered (test environments call create_app() multiple times)
    existing_ids = {m.agent_id for m in global_registry.all_manifests()}

    if "review" not in existing_ids:
        global_registry.register(REVIEW_MANIFEST, ReviewAgentAdapter())
        logger.info("registered_review_agent")

    if "planning" not in existing_ids:
        global_registry.register(PLANNING_MANIFEST, PlanningAgent())
        logger.info("registered_planning_agent")
