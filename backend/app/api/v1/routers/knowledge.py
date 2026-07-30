"""Knowledge Sources API — source catalog and connection CRUD.

GET  /knowledge/overview        → sources + all connections in one trip
GET  /knowledge/sources         → static source catalog
GET  /knowledge/connections     → all configured connections
POST /knowledge/connections     → create a connection
GET  /knowledge/connections/:id → single connection
PUT  /knowledge/connections/:id → update a connection
DELETE /knowledge/connections/:id → remove a connection
POST /knowledge/connections/:id/health → run health check
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.knowledge.registry import KnowledgeSourceSpec, all_sources, require_source
from app.models.github_connection import GitHubConnection
from app.models.knowledge_connection import KnowledgeConnection
from app.models.user import User
from app.schemas.knowledge import (
    ConnectionCreateRequest,
    ConnectionHealthResponse,
    ConnectionInfo,
    ConnectionUpdateRequest,
    KnowledgeOverview,
    KnowledgeSourceInfo,
    TransportInfo,
)
from app.tools.setup import (
    build_tool_for_connection,
    resync_knowledge_connections_for_source,
    sync_knowledge_connection_to_tool,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge-sources"])

# ToolHealth value -> human-readable detail shown next to a connection's
# status badge. Falls back to a title-cased version of the value itself for
# anything not called out here.
_HEALTH_DETAIL = {
    "healthy": "Live check succeeded.",
    "unconfigured": "Missing required configuration.",
    "auth_failed": "Authentication failed — check the credentials.",
    "offline": "Could not reach the server — check the URL.",
    "unavailable": "Server reachable but reported an error.",
    "rate_limited": "Rate limited by the server.",
    "permission_denied": "Authenticated, but the token lacks permission.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_to_info(spec: KnowledgeSourceSpec, connection_count: int) -> KnowledgeSourceInfo:
    return KnowledgeSourceInfo(
        key=spec.key,
        label=spec.label,
        icon=spec.icon,
        description=spec.description,
        capabilities=list(spec.capabilities),
        transports=[
            TransportInfo(
                transport=t.transport.value,
                label=t.label,
                auth_methods=[a.value for a in t.auth_methods],
                auth_fields=t.auth_fields,
            )
            for t in spec.transports
        ],
        available=spec.available,
        connection_count=connection_count,
    )


def _github_connection_to_info(row: GitHubConnection) -> ConnectionInfo:
    """GitHub's real OAuth connection lives in `github_connections` (one row
    per user, see ADR 0006), not `knowledge_connections` — the generic table
    only ever holds PAT/other-transport connections for GitHub. Synthesizing
    a ConnectionInfo here is what lets the Settings UI show GitHub the same
    way it shows every other source, without merging two differently-scoped
    tables (global vs per-user) into one."""
    return ConnectionInfo(
        id=row.id,
        source_type="github",
        name=f"@{row.github_username}",
        transport="rest",
        auth_method="oauth",
        config={},
        scope={},
        enabled=True,
        credentials_configured=True,
        status="healthy",
        status_detail="Connected via OAuth.",
        last_sync_at=row.updated_at,
        last_success_at=row.updated_at,
        latency_ms=None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_info(row: KnowledgeConnection) -> ConnectionInfo:
    return ConnectionInfo(
        id=row.id,
        source_type=row.source_type,
        name=row.name,
        transport=row.transport,
        auth_method=row.auth_method,
        config=row.config or {},
        scope=row.scope or {},
        enabled=row.enabled,
        credentials_configured=bool(row.encrypted_credentials),
        status=row.status,
        status_detail=row.status_detail,
        last_sync_at=row.last_sync_at,
        last_success_at=row.last_success_at,
        latency_ms=row.latency_ms,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=KnowledgeOverview, summary="Full Knowledge Sources view")
async def overview(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> KnowledgeOverview:
    """Everything the Knowledge Sources section needs in one request."""
    rows = (await db.execute(select(KnowledgeConnection))).scalars().all()
    connections = [_row_to_info(r) for r in rows]

    github_connection = (
        await db.execute(
            select(GitHubConnection).where(GitHubConnection.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    if github_connection is not None:
        connections.append(_github_connection_to_info(github_connection))

    # Count connections per source type.
    counts: dict[str, int] = {}
    for conn in connections:
        counts[conn.source_type] = counts.get(conn.source_type, 0) + 1

    sources = [_source_to_info(spec, counts.get(spec.key, 0)) for spec in all_sources()]
    return KnowledgeOverview(sources=sources, connections=connections)


@router.get("/sources", response_model=list[KnowledgeSourceInfo], summary="Source catalog")
async def list_sources(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> list[KnowledgeSourceInfo]:
    rows = (await db.execute(select(KnowledgeConnection))).scalars().all()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.source_type] = counts.get(r.source_type, 0) + 1
    return [_source_to_info(spec, counts.get(spec.key, 0)) for spec in all_sources()]


@router.get("/connections", response_model=list[ConnectionInfo], summary="All connections")
async def list_connections(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    source_type: str | None = None,
) -> list[ConnectionInfo]:
    stmt = select(KnowledgeConnection)
    if source_type:
        stmt = stmt.where(KnowledgeConnection.source_type == source_type)
    rows = (await db.execute(stmt)).scalars().all()
    connections = [_row_to_info(r) for r in rows]

    if source_type is None or source_type == "github":
        github_connection = (
            await db.execute(
                select(GitHubConnection).where(GitHubConnection.user_id == current_user.id)
            )
        ).scalar_one_or_none()
        if github_connection is not None:
            connections.append(_github_connection_to_info(github_connection))

    return connections


@router.post(
    "/connections",
    response_model=ConnectionInfo,
    status_code=status.HTTP_201_CREATED,
    summary="Create a connection",
)
async def create_connection(
    body: ConnectionCreateRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> ConnectionInfo:
    # Validate source type exists.
    require_source(body.source_type)

    encrypted = None
    if body.credentials:
        encrypted = encrypt_secret(json.dumps(body.credentials))

    row = KnowledgeConnection(
        source_type=body.source_type,
        name=body.name,
        transport=body.transport,
        auth_method=body.auth_method,
        config=body.config,
        encrypted_credentials=encrypted,
        scope=body.scope,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Settings → Integrations is where credentials actually get entered —
    # activate the matching Tool Registry entry (Jira, Confluence, ...), if
    # any, immediately rather than requiring a restart. No-op for source
    # types with no corresponding tool.
    sync_knowledge_connection_to_tool(
        body.source_type, body.transport, body.config, body.credentials or {}
    )

    return _row_to_info(row)


@router.get("/connections/{connection_id}", response_model=ConnectionInfo)
async def get_connection(
    connection_id: uuid.UUID,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> ConnectionInfo:
    row = await db.get(KnowledgeConnection, connection_id)
    if row is None:
        raise NotFoundError("Connection not found.")
    return _row_to_info(row)


@router.put("/connections/{connection_id}", response_model=ConnectionInfo)
async def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionUpdateRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> ConnectionInfo:
    row = await db.get(KnowledgeConnection, connection_id)
    if row is None:
        raise NotFoundError("Connection not found.")

    if body.name is not None:
        row.name = body.name
    if body.transport is not None:
        row.transport = body.transport
    if body.auth_method is not None:
        row.auth_method = body.auth_method
    if body.config is not None:
        row.config = body.config
    if body.scope is not None:
        row.scope = body.scope
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.credentials is not None:
        if body.credentials:
            row.encrypted_credentials = encrypt_secret(json.dumps(body.credentials))
        else:
            row.encrypted_credentials = None
        # Credentials changed — reset health.
        row.status = "unknown"
        row.status_detail = None

    await db.commit()
    await db.refresh(row)

    # Re-derive the Tool Registry from the DB rather than pushing this one
    # row's config directly: if the edit just disabled this connection (or
    # left its credentials incomplete), pushing it as-is would activate a
    # broken/disabled connection — or, if it happened to be the connection
    # the registry was already serving, leave the registry stuck on stale
    # config with no way to notice it should fall back to another one.
    await resync_knowledge_connections_for_source(db, row.source_type)

    return _row_to_info(row)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_connection(
    connection_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    row = await db.get(KnowledgeConnection, connection_id)
    if row is not None:
        source_type = row.source_type
        await db.delete(row)
        await db.commit()
        # The Tool Registry holds one live instance per tool, independent of
        # the row that configured it — deleting the row alone left that
        # instance (and the now-deleted connection's decrypted credentials)
        # serving every lookup indefinitely, since nothing else ever
        # re-reads the table until the process restarts. Re-sync immediately
        # so a deleted connection stops being usable the moment it's gone.
        await resync_knowledge_connections_for_source(db, source_type)
        return

    # Not a generic connection — check whether it's the caller's synthetic
    # GitHub OAuth row (see _github_connection_to_info) before giving up.
    github_row = await db.get(GitHubConnection, connection_id)
    if github_row is not None and github_row.user_id == current_user.id:
        await db.delete(github_row)
        await db.commit()
        return

    raise NotFoundError("Connection not found.")


@router.post(
    "/connections/{connection_id}/health",
    response_model=ConnectionHealthResponse,
    summary="Run a health check",
)
async def check_health(
    connection_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> ConnectionHealthResponse:
    """Verify connectivity and credentials for a connection.

    For sources with a matching Tool Registry implementation (Jira,
    Confluence — see app.tools.setup's _KNOWLEDGE_CONNECTION_TOOL_MAP), this
    is a real live probe: an ad-hoc tool instance is built from exactly this
    connection's own credentials and its health_check() is awaited. Every
    other source (Neo4j, filesystem, S3, …) falls back to validating that
    credentials are present — there's no tool-level implementation yet to
    probe live.
    """
    row = await db.get(KnowledgeConnection, connection_id)
    if row is None:
        # The GitHub OAuth row is healthy by construction (its mere
        # existence means the token exchange succeeded) — nothing to probe.
        github_row = await db.get(GitHubConnection, connection_id)
        if github_row is not None and github_row.user_id == current_user.id:
            return ConnectionHealthResponse(
                id=github_row.id,
                status="healthy",
                status_detail="Connected via OAuth.",
                latency_ms=None,
            )
        raise NotFoundError("Connection not found.")

    now = datetime.now(UTC)
    start = time.monotonic()

    credentials: dict[str, Any] = {}
    if row.encrypted_credentials:
        credentials = json.loads(decrypt_secret(row.encrypted_credentials))

    tool = build_tool_for_connection(row.source_type, row.transport, row.config or {}, credentials)

    if tool is None:
        # No tool-backed live check for this source/transport — fall back
        # to validating that credentials are present, same as before.
        if row.auth_method != "none" and not row.encrypted_credentials:
            row.status = "unconfigured"
            row.status_detail = "Credentials not provided."
        else:
            row.status = "healthy"
            row.status_detail = (
                "Configuration valid (not live-checked — no probe implemented for this source)."
            )
            row.last_success_at = now
            row.latency_ms = 0
    else:
        health = await tool.health_check()
        row.status = health.value
        row.status_detail = _HEALTH_DETAIL.get(
            health.value, health.value.replace("_", " ").capitalize()
        )
        row.latency_ms = int((time.monotonic() - start) * 1000)
        if health.value == "healthy":
            row.last_success_at = now

    row.last_sync_at = now
    await db.commit()

    return ConnectionHealthResponse(
        id=row.id,
        status=row.status,
        status_detail=row.status_detail,
        latency_ms=row.latency_ms,
    )
