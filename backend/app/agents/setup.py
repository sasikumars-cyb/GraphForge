"""Agent registration — called once at application startup.

Registers the Review Agent adapter and Planning Agent with the global
registry. Import this module in app/main.py to trigger registration.

Rule: only this file calls global_registry.register(). Agents never
self-register at module import time — that would create import-order
dependencies that are hard to reason about.
"""

from __future__ import annotations

import logging

from app.agents.api_intelligence.agent import ApiIntelligenceAgent
from app.agents.api_intelligence.manifest import API_INTELLIGENCE_MANIFEST
from app.agents.code_generation.agent import CodeGenerationAgent
from app.agents.code_generation.manifest import CODE_GENERATION_MANIFEST
from app.agents.context_discovery.agent import ContextDiscoveryAgent
from app.agents.context_discovery.manifest import CONTEXT_DISCOVERY_MANIFEST
from app.agents.development.agent import DevelopmentAgent
from app.agents.development.manifest import DEVELOPMENT_MANIFEST
from app.agents.documentation.agent import DocumentationReviewAgent
from app.agents.documentation.manifest import DOCUMENTATION_REVIEW_MANIFEST
from app.agents.documentation_health.agent import DocumentationHealthAgent
from app.agents.documentation_health.manifest import DOCUMENTATION_HEALTH_MANIFEST
from app.agents.documentation_planning.agent import DocumentationPlanningAgent
from app.agents.documentation_planning.manifest import DOCUMENTATION_PLANNING_MANIFEST
from app.agents.engineering_review.agent import EngineeringReviewAgent
from app.agents.engineering_review.manifest import ENGINEERING_REVIEW_MANIFEST
from app.agents.git_ops.commit_changes_agent import CommitChangesAgent
from app.agents.git_ops.create_branch_agent import CreateBranchAgent
from app.agents.git_ops.create_pull_request_agent import CreatePullRequestAgent
from app.agents.git_ops.manifests import (
    COMMIT_CHANGES_MANIFEST,
    CREATE_BRANCH_MANIFEST,
    CREATE_PULL_REQUEST_MANIFEST,
    RUN_TESTS_MANIFEST,
)
from app.agents.git_ops.run_tests_agent import TestRunnerAgent
from app.agents.planning.agent import PlanningAgent
from app.agents.planning.manifest import PLANNING_MANIFEST
from app.agents.report_generation.agent import ReportGenerationAgent
from app.agents.report_generation.manifest import REPORT_GENERATION_MANIFEST
from app.agents.review_adapter import REVIEW_MANIFEST, ReviewAgentAdapter
from app.agents.testing.agent import TestPlanningAgent
from app.agents.testing.manifest import TESTING_MANIFEST
from app.orchestrator.registry import global_registry

logger = logging.getLogger(__name__)


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

    if "context_discovery" not in existing_ids:
        global_registry.register(CONTEXT_DISCOVERY_MANIFEST, ContextDiscoveryAgent())
        logger.info("registered_context_discovery_agent")

    if "planning" not in existing_ids:
        global_registry.register(PLANNING_MANIFEST, PlanningAgent())
        logger.info("registered_planning_agent")

    if "development" not in existing_ids:
        global_registry.register(DEVELOPMENT_MANIFEST, DevelopmentAgent())
        logger.info("registered_development_agent")

    if "testing" not in existing_ids:
        global_registry.register(TESTING_MANIFEST, TestPlanningAgent())
        logger.info("registered_testing_agent")

    if "documentation_planning" not in existing_ids:
        global_registry.register(DOCUMENTATION_PLANNING_MANIFEST, DocumentationPlanningAgent())
        logger.info("registered_documentation_planning_agent")

    # Standalone AI Workspace capability (goal=review_documentation) — not a
    # Workflow stage; see app.agents.documentation.manifest's own docstring
    # for how this differs from documentation_planning above.
    if "documentation_review" not in existing_ids:
        global_registry.register(DOCUMENTATION_REVIEW_MANIFEST, DocumentationReviewAgent())
        logger.info("registered_documentation_review_agent")

    # Standalone, read-only AI Workspace capability
    # (goal=analyze_documentation_health) — see its manifest for how it
    # differs from documentation_review above.
    if "documentation_health" not in existing_ids:
        global_registry.register(DOCUMENTATION_HEALTH_MANIFEST, DocumentationHealthAgent())
        logger.info("registered_documentation_health_agent")

    # Standalone, Markdown-only AI Workspace capability
    # (goal=analyze_api_intelligence) — see its manifest for why it has no
    # Neo4j dependency, unlike documentation_review above.
    if "api_intelligence" not in existing_ids:
        global_registry.register(API_INTELLIGENCE_MANIFEST, ApiIntelligenceAgent())
        logger.info("registered_api_intelligence_agent")

    if "engineering_review" not in existing_ids:
        global_registry.register(ENGINEERING_REVIEW_MANIFEST, EngineeringReviewAgent())
        logger.info("registered_engineering_review_agent")

    if "code_generation" not in existing_ids:
        global_registry.register(CODE_GENERATION_MANIFEST, CodeGenerationAgent())
        logger.info("registered_code_generation_agent")

    if "create_branch" not in existing_ids:
        global_registry.register(CREATE_BRANCH_MANIFEST, CreateBranchAgent())
        logger.info("registered_create_branch_agent")

    if "commit_changes" not in existing_ids:
        global_registry.register(COMMIT_CHANGES_MANIFEST, CommitChangesAgent())
        logger.info("registered_commit_changes_agent")

    if "run_tests" not in existing_ids:
        global_registry.register(RUN_TESTS_MANIFEST, TestRunnerAgent())
        logger.info("registered_run_tests_agent")

    if "create_pull_request" not in existing_ids:
        global_registry.register(CREATE_PULL_REQUEST_MANIFEST, CreatePullRequestAgent())
        logger.info("registered_create_pull_request_agent")

    if "report_generation" not in existing_ids:
        global_registry.register(REPORT_GENERATION_MANIFEST, ReportGenerationAgent())
        logger.info("registered_report_generation_agent")
