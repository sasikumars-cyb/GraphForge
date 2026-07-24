"""Agent registration — called once at application startup.

Registers the Review Agent adapter and Planning Agent with the global
registry. Import this module in app/main.py to trigger registration.

Rule: only this file calls global_registry.register(). Agents never
self-register at module import time — that would create import-order
dependencies that are hard to reason about.
"""

from __future__ import annotations

import logging

from app.agents.development.agent import DevelopmentAgent
from app.agents.development.manifest import DEVELOPMENT_MANIFEST
from app.agents.engineering_review.agent import EngineeringReviewAgent
from app.agents.engineering_review.manifest import ENGINEERING_REVIEW_MANIFEST
from app.agents.planning.agent import PlanningAgent
from app.agents.testing.agent import TestPlanningAgent
from app.agents.testing.manifest import TESTING_MANIFEST

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

    if "development" not in existing_ids:
        global_registry.register(DEVELOPMENT_MANIFEST, DevelopmentAgent())
        logger.info("registered_development_agent")

    if "testing" not in existing_ids:
        global_registry.register(TESTING_MANIFEST, TestPlanningAgent())
        logger.info("registered_testing_agent")

    if "engineering_review" not in existing_ids:
        global_registry.register(ENGINEERING_REVIEW_MANIFEST, EngineeringReviewAgent())
        logger.info("registered_engineering_review_agent")
