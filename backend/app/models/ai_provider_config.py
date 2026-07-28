"""AI provider configuration tables.

Two tables:

- `ai_provider_configs` — one row per configured provider, holding the
  encrypted API key and that provider's chosen model/parameters.
- `ai_settings` — a single row of global defaults (default provider/model,
  shared generation parameters, stage overrides, fallback order).

Secrets follow the exact pattern already established by
`GitHubConnection.encrypted_access_token`: encrypted at rest with
`app.core.crypto` (Fernet) and never returned to the frontend.

These tables are *optional*. When empty, the configuration layer falls back
to environment variables, so existing installations behave identically until
someone configures a provider in the UI.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AIProviderConfig(Base):
    """One configured AI provider."""

    __tablename__ = "ai_provider_configs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Matches ProviderSpec.key in app.ai.providers.registry. Unique because a
    # provider is configured once for the installation, not per user — AI
    # provider credentials are an operator concern, unlike GitHub connections
    # which are per-user identity.
    provider_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    # Encrypted with app.core.crypto (Fernet). Never logged, never serialized
    # to an API response. Null is legitimate for providers that need no key
    # (e.g. a local Ollama instance).
    encrypted_api_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Per-provider overrides. Null means "use the global default".
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Health, written by validation and by real request outcomes ──
    # Free-form string rather than an enum column so a new status does not
    # require a migration: ready | rate_limited | auth_failed | offline |
    # validation_failed | unknown
    status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AISettings(Base):
    """Global AI defaults — a single row (singleton pattern)."""

    __tablename__ = "ai_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # The default AI Profile. Takes precedence over default_provider/model —
    # profiles are the user-facing abstraction, the raw provider fields remain
    # as the lower-level fallback for installs that never create a profile.
    default_profile_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)

    default_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # {"planning": {"provider": "anthropic", "model": "...", "temperature": 0.1}, ...}
    # JSONB rather than columns so adding a stage or an overridable parameter
    # never needs a migration.
    stage_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict[str, Any], nullable=False
    )

    # Ordered provider keys tried after a *recoverable* failure. Empty means
    # fallback is disabled, which is the default — a run must not silently
    # cross vendors unless an operator asked for it.
    fallback_order: Mapped[list[Any]] = mapped_column(JSONB, default=list[Any], nullable=False)
    fallback_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
