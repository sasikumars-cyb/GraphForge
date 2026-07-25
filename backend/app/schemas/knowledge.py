"""DTOs for the Knowledge Sources API.

Security invariant: credentials are write-only — never returned in responses.
The boolean `credentials_configured` signals presence without exposing values.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Source catalog (static, from registry)
# ---------------------------------------------------------------------------


class TransportInfo(BaseModel):
    transport: str
    label: str
    auth_methods: list[str] = Field(default_factory=list)
    auth_fields: dict[str, list[str]] = Field(default_factory=dict)


class KnowledgeSourceInfo(BaseModel):
    """A source type as seen by the UI — rendered generically."""

    key: str
    label: str
    icon: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    transports: list[TransportInfo] = Field(default_factory=list)
    available: bool = True
    connection_count: int = 0


# ---------------------------------------------------------------------------
# Connection (instance)
# ---------------------------------------------------------------------------


class ConnectionInfo(BaseModel):
    """One configured connection — the UI renders this per-instance."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: str
    name: str
    transport: str
    auth_method: str
    config: dict = Field(default_factory=dict)
    scope: dict = Field(default_factory=dict)
    enabled: bool = True
    credentials_configured: bool = False

    # Health
    status: str = "unknown"
    status_detail: str | None = None
    last_sync_at: datetime | None = None
    last_success_at: datetime | None = None
    latency_ms: int | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConnectionCreateRequest(BaseModel):
    """Create a new connection to a knowledge source."""

    source_type: str
    name: str
    transport: str
    auth_method: str
    config: dict = Field(default_factory=dict)
    credentials: dict = Field(default_factory=dict)  # write-only
    scope: dict = Field(default_factory=dict)


class ConnectionUpdateRequest(BaseModel):
    """Update an existing connection. Omitted fields are unchanged."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    transport: str | None = None
    auth_method: str | None = None
    config: dict | None = None
    credentials: dict | None = None  # write-only; omit to keep current
    scope: dict | None = None
    enabled: bool | None = None


class ConnectionHealthResponse(BaseModel):
    id: uuid.UUID
    status: str
    status_detail: str | None = None
    latency_ms: int | None = None


# ---------------------------------------------------------------------------
# Overview (one-shot load for the UI)
# ---------------------------------------------------------------------------


class KnowledgeOverview(BaseModel):
    """Everything the Knowledge Sources section needs in one round trip."""

    sources: list[KnowledgeSourceInfo]
    connections: list[ConnectionInfo]
