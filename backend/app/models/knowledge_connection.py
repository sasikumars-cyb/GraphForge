"""Knowledge Connections — configured instances of knowledge sources.

Each row represents one connection to a specific deployment of a source
system (e.g. "Production GitHub", "Engineering Jira"). Multiple connections
per source type are supported.

Authentication credentials are encrypted at rest (Fernet) following the
same pattern as `github_connections.encrypted_access_token` and
`ai_provider_configs.encrypted_api_key`.

The `config` JSONB column holds transport-specific settings (base_url,
region, bucket, etc.). The `scope` JSONB column holds what the connection
can access (selected repos, project keys, spaces, etc.).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class KnowledgeConnection(Base):
    """One configured connection to a knowledge source deployment."""

    __tablename__ = "knowledge_connections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Which source type this connects to (matches KnowledgeSourceSpec.key).
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # User-facing name for this connection (e.g. "Production", "Open Source").
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Transport used (rest, graphql, mcp, sdk, filesystem, database).
    transport: Mapped[str] = mapped_column(String(32), nullable=False)

    # Auth method used (pat, oauth, basic, api_key, etc.).
    auth_method: Mapped[str] = mapped_column(String(32), nullable=False)

    # Transport-specific configuration (base_url, region, endpoint, etc.).
    # Never contains secrets — those go in encrypted_credentials.
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Encrypted credentials blob (JSON serialized then Fernet-encrypted).
    # Contains tokens, passwords, private keys — never logged or returned.
    encrypted_credentials: Mapped[str | None] = mapped_column(String(4096), nullable=True)

    # What this connection provides access to (repos, projects, spaces).
    scope: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Operational state.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Health ──
    status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
