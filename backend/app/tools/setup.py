"""Tool registration — called once at application startup.

Mirrors app/agents/setup.py: this is the single place that calls
registry.register(). Tool implementations are never self-registered at
import time.
"""

from __future__ import annotations

import logging

from app.tools.interfaces import ToolCategory
from app.tools.registry import ToolSpec, get_tool_registry

logger = logging.getLogger(__name__)


def register_all_tools() -> None:
    """Register all tool specs with the global ToolRegistry.

    Idempotent: re-registration replaces the spec but does not create
    duplicate instances. Safe to call multiple times (test environments).
    """
    registry = get_tool_registry()

    # ------------------------------------------------------------------
    # Internal tools — enabled by default, no auth required
    # ------------------------------------------------------------------

    from app.tools.implementations.neo4j_tool import Neo4jGraphTool

    registry.register(
        ToolSpec(
            tool_id="neo4j_graph",
            display_name="Knowledge Graph (Neo4j)",
            description=(
                "Reads the Neo4j Knowledge Graph for indexed repositories, "
                "software components, and Kafka topics. Primary source of "
                "ground-truth architecture facts for the Planning Agent."
            ),
            category=ToolCategory.GRAPH,
            capabilities=[
                "repository_discovery",
                "component_graph",
                "kafka_topics",
                "architecture_context",
            ],
            factory=lambda cfg: Neo4jGraphTool(cfg),
            requires_auth=False,
            default_enabled=True,
            icon="🗄️",
        )
    )

    # ------------------------------------------------------------------
    # External tools — require auth; enabled=False until configured
    # ------------------------------------------------------------------

    from app.tools.implementations.github_tool import GitHubTool

    registry.register(
        ToolSpec(
            tool_id="github",
            display_name="GitHub",
            description=(
                "Fetches pull request metadata, open issues, and repository "
                "activity from the GitHub API."
            ),
            category=ToolCategory.CODE_INTELLIGENCE,
            capabilities=["pull_requests", "issues", "repository_activity"],
            factory=lambda cfg: GitHubTool(cfg),
            requires_auth=True,
            auth_fields=["github_token", "github_api_url"],
            default_enabled=False,
            icon="🐙",
            notes="Requires a GitHub personal access token with repo read scope.",
        )
    )

    from app.tools.implementations.jira_tool import JiraTool

    registry.register(
        ToolSpec(
            tool_id="jira",
            display_name="Jira",
            description=(
                "Fetches issues, epics, and sprint metadata from Jira to align "
                "plans with active tickets."
            ),
            category=ToolCategory.PROJECT_MANAGEMENT,
            capabilities=["issues", "epics", "sprints", "project_management"],
            factory=lambda cfg: JiraTool(cfg),
            requires_auth=True,
            auth_fields=["jira_base_url", "jira_email", "jira_api_token"],
            default_enabled=False,
            icon="📋",
            notes="Requires a Jira API token and base URL (e.g. https://myorg.atlassian.net).",
        )
    )

    from app.tools.implementations.confluence_tool import ConfluenceTool

    registry.register(
        ToolSpec(
            tool_id="confluence",
            display_name="Confluence",
            description=(
                "Fetches design documents, ADRs, and runbooks from Confluence."
            ),
            category=ToolCategory.DOCUMENTATION,
            capabilities=["design_documents", "adrs", "runbooks", "documentation"],
            factory=lambda cfg: ConfluenceTool(cfg),
            requires_auth=True,
            auth_fields=["confluence_base_url", "confluence_email", "confluence_api_token"],
            default_enabled=False,
            icon="📚",
            notes="Requires a Confluence API token and base URL.",
        )
    )

    logger.info("tool_registration_complete tool_count=%d", len(registry.all_specs()))
