"""Tool registration — called once at application startup.

Mirrors app/agents/setup.py: this is the single place that calls
registry.register(). Tool implementations are never self-registered at
import time.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.tools.interfaces import ITool, ToolCategory
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
                "jira_base_url",
                "jira_email",
                "jira_api_token",
                "jira_mcp_server_url",
                "jira_mcp_api_key",
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
            description=("Fetches design documents, ADRs, and runbooks from Confluence."),
            category=ToolCategory.DOCUMENTATION,
            capabilities=["design_documents", "adrs", "runbooks", "documentation"],
            factory=lambda cfg: ConfluenceTool(cfg),
            requires_auth=True,
            # Both transports are fully functional: REST searches via CQL
            # (Confluence Query Language) against /wiki/rest/api/search;
            # MCP is attempted first when configured, falling back to REST
            # on a recoverable failure (see ConfluenceTool.execute).
            auth_fields=[
                "confluence_base_url",
                "confluence_email",
                "confluence_api_token",
                "confluence_mcp_server_url",
                "confluence_mcp_api_key",
            ],
            default_enabled=False,
            icon="📚",
            notes=(
                "REST: Confluence base URL + email + API token (CQL search). Or "
                "MCP: a Confluence MCP server URL (+ optional API key) — set "
                "confluence_mcp_server_url to try it first, with automatic "
                "fallback to REST."
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
#
# There is no source-specific map here anymore: every piece of translation
# metadata (which tool_id a transport activates, its credential field map,
# and whether/how to auto-wire a REST credential onto a known MCP endpoint)
# lives on that source's own `TransportSpec` in `app.knowledge.registry` —
# reached via `get_source(source_type).transports`. Adding a new MCP-backed
# source is exactly Part 3's two steps: one registry entry (with its
# TransportSpecs' `tool_id`/`credential_field_map`/`auto_wire_credential`
# filled in) plus one `ITool` implementation — nothing in this module
# changes.


def _build_tool_config(
    source_type: str, transport: str, config: dict[str, Any], credentials: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    """Translate one Knowledge Connection's generic fields into a tool's own
    config dict, using the matching `TransportSpec`'s metadata as the sole
    source of truth. Returns None when the source/transport has no matching
    tool (`tool_id` unset), or the connection's required fields aren't all
    present yet. Shared by the registry-sync path and the ad-hoc
    per-connection path below — the two differ only in what they do with
    the resulting (tool_id, config).
    """
    from app.knowledge.registry import Transport, get_source

    source_spec = get_source(source_type)
    if source_spec is None:
        return None
    transport_spec = next((t for t in source_spec.transports if t.transport == transport), None)
    if transport_spec is None or transport_spec.tool_id is None or not transport_spec.credential_field_map:
        return None
    tool_id = transport_spec.tool_id
    field_map = transport_spec.credential_field_map

    merged = {**config, **credentials}
    tool_config = {
        tool_key: merged[conn_key]
        for conn_key, tool_key in field_map.items()
        if merged.get(conn_key)
    }
    if len(tool_config) != len(field_map):
        logger.info(
            "tool_sync_incomplete_config tool=%s source_type=%s transport=%s",
            tool_id,
            source_type,
            transport,
        )
        return None

    # Auto-wire: reuse this REST connection's own credential as the MCP
    # bearer token against the sibling MCP TransportSpec's known endpoint —
    # no second connection, no extra field in the Integrations UI. Declared
    # per-source via `auto_wire_credential`/`known_mcp_endpoint` rather than
    # a second hardcoded map (see knowledge/registry.py's TransportSpec).
    if transport == Transport.REST and transport_spec.auto_wire_credential:
        mcp_spec = next((t for t in source_spec.transports if t.transport == Transport.MCP), None)
        if mcp_spec is not None and mcp_spec.known_mcp_endpoint:
            token = merged.get(transport_spec.auto_wire_credential)
            server_url_key = mcp_spec.credential_field_map.get("server_url")
            api_key_key = mcp_spec.credential_field_map.get("api_key")
            if token and server_url_key and api_key_key:
                tool_config[server_url_key] = mcp_spec.known_mcp_endpoint
                tool_config[api_key_key] = token
                logger.info(
                    "tool_sync_mcp_auto_wired tool=%s endpoint=%s",
                    tool_id,
                    mcp_spec.known_mcp_endpoint,
                )

    return tool_id, tool_config


def sync_knowledge_connection_to_tool(
    source_type: str, transport: str, config: dict[str, Any], credentials: dict[str, Any]
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


def build_tool_for_connection(
    source_type: str, transport: str, config: dict[str, Any], credentials: dict[str, Any]
) -> ITool | None:
    """Construct a fresh, throwaway tool instance for exactly this
    connection's own credentials — not the Tool Registry singleton.

    The registry holds one instance per tool_id, last-write-wins across
    however many Knowledge Connections share that source_type. A live health
    check must prove *this* connection's credentials work, not whichever
    connection happened to sync into the registry most recently — so this
    builds an independent instance instead of reading `get_tool_registry().
    get_tool()`.

    The constructor itself, though, comes from the same `ToolSpec.factory`
    `register_all_tools()` already registered — no second tool_id ->
    constructor map to keep in sync with the first. Only tools already
    registered there (and therefore already reachable via the Tool Registry
    UI) can ever be built this way.
    """
    built = _build_tool_config(source_type, transport, config, credentials)
    if built is None:
        return None
    tool_id, tool_config = built
    spec = next((s for s in get_tool_registry().all_specs() if s.tool_id == tool_id), None)
    if spec is None:
        return None
    return spec.factory(tool_config)


async def sync_all_knowledge_connections_to_tools(db: AsyncSession) -> None:
    """Startup backfill: activate tools for Knowledge Connections created in
    an earlier process (before this sync existed, or before a restart)."""
    from sqlalchemy import select

    from app.core.crypto import decrypt_secret
    from app.models.knowledge_connection import KnowledgeConnection

    rows = (await db.execute(select(KnowledgeConnection))).scalars().all()
    for row in rows:
        credentials: dict[str, Any] = {}
        if row.encrypted_credentials:
            try:
                credentials = json.loads(decrypt_secret(row.encrypted_credentials))
            except Exception:
                logger.warning(
                    "tool_sync_decrypt_failed connection_id=%s source_type=%s",
                    row.id,
                    row.source_type,
                )
                continue
        sync_knowledge_connection_to_tool(
            row.source_type, row.transport, row.config or {}, credentials
        )
