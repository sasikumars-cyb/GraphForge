"""Tool registration — called once at application startup.

Mirrors app/agents/setup.py: this is the single place that calls
registry.register(). Tool implementations are never self-registered at
import time.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.tools.interfaces import ToolCategory
from app.tools.registry import ToolSpec, get_tool_registry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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


# ---------------------------------------------------------------------------
# Knowledge Connection → Tool Registry sync
# ---------------------------------------------------------------------------
#
# Settings → Integrations ("Knowledge Connections") is where a user actually
# enters Jira/Confluence credentials today — the Tool Registry's own
# `configure()` (Settings → Tool Registry) is a second, separate admin-only
# config surface for the same tools. Rather than make the user configure
# Jira twice, a Knowledge Connection for a source that has a matching Tool
# Registry entry auto-activates that tool: this is the one function that
# does the field-name translation (KnowledgeConnection's generic
# "base_url"/"email"/"api_token" → each tool's own prefixed config keys)
# and calls registry.configure(). Called after a connection is created/
# updated (knowledge.py) and once at startup to pick up connections made in
# an earlier process (see app.main's lifespan).

# tool_id -> KnowledgeConnection source_type this tool corresponds to, and
# the KnowledgeConnection field name -> this tool's own config key.
_KNOWLEDGE_CONNECTION_TOOL_MAP: dict[str, tuple[str, dict[str, str]]] = {
    "jira": ("jira", {
        "base_url": "jira_base_url",
        "email": "jira_email",
        "api_token": "jira_api_token",
    }),
    "confluence": ("confluence", {
        "base_url": "confluence_base_url",
        "email": "confluence_email",
        "api_token": "confluence_api_token",
    }),
}


def sync_knowledge_connection_to_tool(source_type: str, config: dict, credentials: dict) -> None:
    """Activate the Tool Registry entry matching a Knowledge Connection, if any.

    No-op for source types with no corresponding tool (e.g. neo4j, filesystem)
    or when the required fields aren't all present yet.
    """
    for tool_id, (mapped_source_type, field_map) in _KNOWLEDGE_CONNECTION_TOOL_MAP.items():
        if mapped_source_type != source_type:
            continue

        merged = {**config, **credentials}
        tool_config = {
            tool_key: merged[conn_key] for conn_key, tool_key in field_map.items() if merged.get(conn_key)
        }
        if len(tool_config) != len(field_map):
            logger.info(
                "tool_sync_incomplete_config tool=%s source_type=%s", tool_id, source_type
            )
            return

        get_tool_registry().configure(tool_id, enabled=True, config=tool_config)
        logger.info("tool_sync_configured tool=%s source_type=%s", tool_id, source_type)
        return


async def sync_all_knowledge_connections_to_tools(db: "AsyncSession") -> None:
    """Startup backfill: activate tools for Knowledge Connections created in
    an earlier process (before this sync existed, or before a restart)."""
    from sqlalchemy import select

    from app.core.crypto import decrypt_secret
    from app.models.knowledge_connection import KnowledgeConnection

    rows = (await db.execute(select(KnowledgeConnection))).scalars().all()
    for row in rows:
        credentials: dict = {}
        if row.encrypted_credentials:
            try:
                credentials = json.loads(decrypt_secret(row.encrypted_credentials))
            except Exception:
                logger.warning(
                    "tool_sync_decrypt_failed connection_id=%s source_type=%s",
                    row.id, row.source_type,
                )
                continue
        sync_knowledge_connection_to_tool(row.source_type, row.config or {}, credentials)
