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
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.crypto import encrypt_secret
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.knowledge.registry import all_sources, require_source
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

router = APIRouter(prefix="/knowledge", tags=["knowledge-sources"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_to_info(spec, connection_count: int) -> KnowledgeSourceInfo:
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
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> KnowledgeOverview:
    """Everything the Knowledge Sources section needs in one request."""
    rows = (await db.execute(select(KnowledgeConnection))).scalars().all()
    connections = [_row_to_info(r) for r in rows]

    # Count connections per source type.
    counts: dict[str, int] = {}
    for conn in connections:
        counts[conn.source_type] = counts.get(conn.source_type, 0) + 1

    sources = [_source_to_info(spec, counts.get(spec.key, 0)) for spec in all_sources()]
    return KnowledgeOverview(sources=sources, connections=connections)


@router.get("/sources", response_model=list[KnowledgeSourceInfo], summary="Source catalog")
async def list_sources(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[KnowledgeSourceInfo]:
    rows = (await db.execute(select(KnowledgeConnection))).scalars().all()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.source_type] = counts.get(r.source_type, 0) + 1
    return [_source_to_info(spec, counts.get(spec.key, 0)) for spec in all_sources()]


@router.get("/connections", response_model=list[ConnectionInfo], summary="All connections")
async def list_connections(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    source_type: str | None = None,
) -> list[ConnectionInfo]:
    stmt = select(KnowledgeConnection)
    if source_type:
        stmt = stmt.where(KnowledgeConnection.source_type == source_type)
    rows = (await db.execute(stmt)).scalars().all()
    return [_row_to_info(r) for r in rows]


@router.post(
    "/connections",
    response_model=ConnectionInfo,
    status_code=status.HTTP_201_CREATED,
    summary="Create a connection",
)
async def create_connection(
    body: ConnectionCreateRequest,
    _: User = Depends(get_current_user),
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
    return _row_to_info(row)


@router.get("/connections/{connection_id}", response_model=ConnectionInfo)
async def get_connection(
    connection_id: uuid.UUID,
    _: User = Depends(get_current_user),
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
    _: User = Depends(get_current_user),
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
    return _row_to_info(row)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_connection(
    connection_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    row = await db.get(KnowledgeConnection, connection_id)
    if row is None:
        raise NotFoundError("Connection not found.")
    await db.delete(row)
    await db.commit()


@router.post(
    "/connections/{connection_id}/health",
    response_model=ConnectionHealthResponse,
    summary="Run a health check",
)
async def check_health(
    connection_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ConnectionHealthResponse:
    """Verify connectivity and credentials for a connection.

    In this release the health check validates configuration presence.
    Transport-specific live checks (HTTP ping, DB connect, etc.) are a
    follow-up — the architecture supports them via the transport/auth_method
    dispatch that the source registry declares.
    """
    row = await db.get(KnowledgeConnection, connection_id)
    if row is None:
        raise NotFoundError("Connection not found.")

    now = datetime.now(UTC)

    # Basic validation: credentials present if auth requires them.
    if row.auth_method != "none" and not row.encrypted_credentials:
        row.status = "unconfigured"
        row.status_detail = "Credentials not provided."
    else:
        row.status = "healthy"
        row.status_detail = "Configuration valid."
        row.last_success_at = now
        row.latency_ms = 0  # Placeholder until live transport checks.

    row.last_sync_at = now
    await db.commit()

    return ConnectionHealthResponse(
        id=row.id,
        status=row.status,
        status_detail=row.status_detail,
        latency_ms=row.latency_ms,
    )
