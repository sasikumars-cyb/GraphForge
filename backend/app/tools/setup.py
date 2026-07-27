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

    # Registered for the Tool Registry UI's listing/health-check surface
    # only - never `configure()`d with a token, and deliberately absent
    # from tools/setup.py's Knowledge-Connection sync map. GitHub access is
    # per-user (an OAuth connection - see app.models.github_connection),
    # not an install-wide credential like Jira/Confluence's, so the
    # singleton instance here always stays unconfigured. The Planning Agent
    # builds a real, per-run GitHubTool instance using the current run's
    # own user's token instead - see PlanningAgent's GitHub enrichment
    # block and ToolExecutor.execute_instance().
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
            notes=(
                "Configured per-run from each user's connected GitHub account "
                "(Settings → Integrations), not here."
            ),
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
            # Either the REST fields or just jira_mcp_server_url — JiraTool
            # picks MCP over REST when the latter is present. Listed
            # together since this is the one admin-configuration surface
            # (Settings -> Tool Registry) that offers both; Settings ->
            # Integrations only ever supplies one, per whichever transport
            # the Knowledge Connection was created with.
            auth_fields=[
                "jira_base_url", "jira_email", "jira_api_token",
                "jira_mcp_server_url", "jira_mcp_api_key",
            ],
            default_enabled=False,
            icon="📋",
            notes=(
                "REST: Jira base URL + email + API token. Or MCP: a Jira MCP "
                "server URL (+ optional API key) - set jira_mcp_server_url to "
                "use it instead."
            ),
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
            # REST fields are stored/health-checked but execute() has no REST
            # path yet — search needs a query DSL (CQL) this tool doesn't
            # build. MCP is the only working transport today.
            auth_fields=[
                "confluence_base_url", "confluence_email", "confluence_api_token",
                "confluence_mcp_server_url", "confluence_mcp_api_key",
            ],
            default_enabled=False,
            icon="📚",
            notes=(
                "MCP: a Confluence MCP server URL (+ optional API key). REST "
                "credentials are stored but search is not yet implemented."
            ),
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

# (source_type, transport) -> (tool_id, field map). Two entries per source
# that supports MCP (see knowledge/registry.py's TransportSpec declarations)
# — REST and MCP land on the *same* tool_id, since a tool's config keys are
# how it decides which transport to actually use internally (e.g.
# JiraTool.__init__ picks MCP over REST purely based on which keys are
# present). Adding a new MCP-backed source is: one more entry here, plus
# whatever the tool implementation itself needs.
_KNOWLEDGE_CONNECTION_TOOL_MAP: dict[tuple[str, str], tuple[str, dict[str, str]]] = {
    ("jira", "rest"): ("jira", {
        "base_url": "jira_base_url",
        "email": "jira_email",
        "api_token": "jira_api_token",
    }),
    ("jira", "mcp"): ("jira", {
        "server_url": "jira_mcp_server_url",
        "api_key": "jira_mcp_api_key",
    }),
    ("confluence", "rest"): ("confluence", {
        "base_url": "confluence_base_url",
        "email": "confluence_email",
        "api_token": "confluence_api_token",
    }),
    ("confluence", "mcp"): ("confluence", {
        "server_url": "confluence_mcp_server_url",
        "api_key": "confluence_mcp_api_key",
    }),
}

# Auto-wires a REST connection's own credential into the same tool's MCP
# config keys, so the tool can try the vendor's known MCP server first and
# fall back to the REST path it was already using - no second connection,
# no extra field in the Integrations UI. Only applies when the source has a
# `known_mcp_endpoint` configured (knowledge/registry.py's TransportSpec,
# sourced from app.core.config's jira/confluence_mcp_default_server_url —
# both point at Atlassian's official hosted MCP server, confirmed to accept
# a plain bearer-token API key, not OAuth-only as originally assumed).
# source_type -> (rest credential field to reuse, mcp_server_url key, mcp_api_key key)
_MCP_AUTO_WIRE: dict[str, tuple[str, str, str]] = {
    "jira": ("api_token", "jira_mcp_server_url", "jira_mcp_api_key"),
    "confluence": ("api_token", "confluence_mcp_server_url", "confluence_mcp_api_key"),
}


def _build_tool_config(
    source_type: str, transport: str, config: dict, credentials: dict
) -> tuple[str, dict] | None:
    """Translate one Knowledge Connection's generic fields into a tool's own
    config dict. Returns None when there's no matching tool, or the
    connection's required fields aren't all present yet. Shared by the
    registry-sync path and the ad-hoc per-connection path below — the two
    differ only in what they do with the resulting (tool_id, config).
    """
    mapping = _KNOWLEDGE_CONNECTION_TOOL_MAP.get((source_type, transport))
    if mapping is None:
        return None
    tool_id, field_map = mapping

    merged = {**config, **credentials}
    tool_config = {
        tool_key: merged[conn_key] for conn_key, tool_key in field_map.items() if merged.get(conn_key)
    }
    if len(tool_config) != len(field_map):
        logger.info(
            "tool_sync_incomplete_config tool=%s source_type=%s transport=%s",
            tool_id, source_type, transport,
        )
        return None

    if transport == "rest":
        auto_wire = _MCP_AUTO_WIRE.get(source_type)
        if auto_wire is not None:
            cred_field, server_url_key, api_key_key = auto_wire
            from app.knowledge.registry import get_source

            source_spec = get_source(source_type)
            known_endpoint = next(
                (
                    t.known_mcp_endpoint
                    for t in (source_spec.transports if source_spec else ())
                    if t.transport == "mcp" and t.known_mcp_endpoint
                ),
                None,
            )
            token = merged.get(cred_field)
            if known_endpoint and token:
                tool_config[server_url_key] = known_endpoint
                tool_config[api_key_key] = token
                logger.info("tool_sync_mcp_auto_wired tool=%s endpoint=%s", tool_id, known_endpoint)

    return tool_id, tool_config


def sync_knowledge_connection_to_tool(
    source_type: str, transport: str, config: dict, credentials: dict
) -> None:
    """Activate the Tool Registry entry matching a Knowledge Connection, if any.

    No-op for (source_type, transport) combinations with no corresponding
    tool config (e.g. neo4j, filesystem, or a transport not yet mapped) or
    when the required fields aren't all present yet.
    """
    built = _build_tool_config(source_type, transport, config, credentials)
    if built is None:
        return
    tool_id, tool_config = built

    get_tool_registry().configure(tool_id, enabled=True, config=tool_config)
    logger.info(
        "tool_sync_configured tool=%s source_type=%s transport=%s", tool_id, source_type, transport
    )


_TOOL_FACTORIES: dict[str, "Callable[[dict], object]"] = {}


def _tool_factories() -> dict[str, "Callable[[dict], object]"]:
    """Lazily built tool_id -> constructor map, for the ad-hoc path below.
    Local imports on first use, same reasoning as register_all_tools()."""
    if not _TOOL_FACTORIES:
        from app.tools.implementations.confluence_tool import ConfluenceTool
        from app.tools.implementations.jira_tool import JiraTool

        _TOOL_FACTORIES["jira"] = JiraTool
        _TOOL_FACTORIES["confluence"] = ConfluenceTool
    return _TOOL_FACTORIES


def build_tool_for_connection(
    source_type: str, transport: str, config: dict, credentials: dict
) -> object | None:
    """Construct a fresh, throwaway tool instance for exactly this
    connection's own credentials — not the Tool Registry singleton.

    The registry holds one instance per tool_id, last-write-wins across
    however many Knowledge Connections share that source_type. A live health
    check must prove *this* connection's credentials work, not whichever
    connection happened to sync into the registry most recently — so this
    builds an independent instance instead of reading `get_tool_registry()`.
    """
    built = _build_tool_config(source_type, transport, config, credentials)
    if built is None:
        return None
    tool_id, tool_config = built
    factory = _tool_factories().get(tool_id)
    if factory is None:
        return None
    return factory(tool_config)


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
        sync_knowledge_connection_to_tool(row.source_type, row.transport, row.config or {}, credentials)
