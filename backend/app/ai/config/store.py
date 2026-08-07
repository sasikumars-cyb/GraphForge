"""In-memory snapshot of AI provider configuration.

Why a snapshot
--------------
`create_llm_provider()` is synchronous and is called from ~20 places across
the agents. Reading provider config from Postgres at call time would make it
async and force every one of those call sites to change — a large, risky diff
for no user-visible benefit.

Instead the DB state is loaded once into an immutable snapshot that sync code
can read. Every write through the settings API (`app.api.v1.routers.
ai_workspace`) calls `refresh()` directly — not the separate `invalidate()`
below, despite what this module's history might suggest; `refresh()` both
drops the stale snapshot and re-populates it in the same step, so the next
resolution picks up the change immediately, with no window where the
snapshot sits empty waiting for something else to reload it. That is what
delivers "switch providers and models without restarting" while keeping the
change set small.

The one other place a valid snapshot has to exist without a write ever
having happened yet: process start. `app.main`'s lifespan calls `refresh()`
once at startup, for the same reason it recovers orphaned runs and reclaims
expired job leases there — `resolver.resolve()` reads `current_snapshot()`
directly (it must stay synchronous; see its own docstring), so on a fresh
process with nothing yet forcing a load, an agent run started before any
`ai_workspace` request would resolve against an empty, `loaded=False`
snapshot and silently fall through to the environment-tier provider instead
of whatever was actually configured in the UI.

The snapshot holds decrypted keys in memory only. They are never serialized,
never logged, and never returned by an API response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenDecryptionError, decrypt_secret
from app.models.ai_profile import AIProfile
from app.models.ai_provider_config import AIProviderConfig, AISettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderRecord:
    """One configured provider, as the resolver sees it."""

    provider_key: str
    api_key: str | None
    model: str | None
    base_url: str | None
    temperature: float | None
    max_tokens: int | None
    enabled: bool
    status: str
    provider_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProfileRecord:
    """One AI Profile, as the resolver sees it."""

    slug: str
    name: str
    provider_key: str
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_level: str | None = None
    structured_output: bool = True
    streaming: bool = False
    fallback_profile_slug: str | None = None


@dataclass(frozen=True)
class ConfigSnapshot:
    """Immutable view of stored AI configuration.

    `loaded` distinguishes "no configuration exists" (fall back to env) from
    "configuration exists and is empty" — without it a fresh install would be
    indistinguishable from one where every provider was deliberately removed.
    """

    providers: dict[str, ProviderRecord] = field(default_factory=dict)
    profiles: dict[str, ProfileRecord] = field(default_factory=dict)
    default_profile_slug: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stage_overrides: dict[str, Any] = field(default_factory=dict[str, Any])
    fallback_order: list[Any] = field(default_factory=list[Any])
    fallback_enabled: bool = False
    loaded: bool = False

    def provider(self, key: str) -> ProviderRecord | None:
        return self.providers.get(key)

    def profile(self, slug: str) -> ProfileRecord | None:
        return self.profiles.get(slug)

    def configured_keys(self) -> list[str]:
        return [k for k, r in self.providers.items() if r.enabled]

    def stage_profile(self, stage: str | None) -> str | None:
        """Profile slug mapped to a workflow stage, if any."""
        if not stage:
            return None
        entry = self.stage_overrides.get(stage) or {}
        if isinstance(entry, dict) and entry.get("profile"):
            return str(entry["profile"])
        # A bare string mapping ({"planning": "fast-planner"}) is accepted as
        # shorthand, since that is the form the UI writes for stage mapping.
        if isinstance(entry, str) and entry:
            return entry
        return None


_snapshot: ConfigSnapshot = ConfigSnapshot()


def current_snapshot() -> ConfigSnapshot:
    """The active snapshot. Empty (`loaded=False`) until `refresh()` runs."""
    return _snapshot


def invalidate() -> None:
    """Drop the snapshot so the next `refresh()` re-reads the database.

    Called by the settings API after any write — this is what makes config
    changes take effect without a restart.
    """
    global _snapshot
    _snapshot = ConfigSnapshot()


async def refresh(db: AsyncSession) -> ConfigSnapshot:
    """Reload the snapshot from the database and publish it."""
    global _snapshot

    providers: dict[str, ProviderRecord] = {}
    rows = (await db.execute(select(AIProviderConfig))).scalars().all()
    for row in rows:
        api_key: str | None = None
        if row.encrypted_api_key:
            try:
                api_key = decrypt_secret(row.encrypted_api_key)
            except TokenDecryptionError:
                # A rotated TOKEN_ENCRYPTION_KEY must not take the whole
                # platform down: drop this provider and surface it as broken
                # rather than raising during resolution.
                logger.warning("ai_provider_key_undecryptable provider=%s", row.provider_key)
                api_key = None
        providers[row.provider_key] = ProviderRecord(
            provider_key=row.provider_key,
            api_key=api_key,
            model=row.model,
            base_url=row.base_url,
            temperature=row.temperature,
            max_tokens=row.max_tokens,
            enabled=row.enabled,
            status=row.status,
            provider_options=dict(row.provider_options or {}),
        )

    profiles: dict[str, ProfileRecord] = {}
    profile_rows = (await db.execute(select(AIProfile))).scalars().all()
    for p in profile_rows:
        profiles[p.slug] = ProfileRecord(
            slug=p.slug,
            name=p.name,
            provider_key=p.provider_key,
            model=p.model,
            temperature=p.temperature,
            max_tokens=p.max_tokens,
            reasoning_level=p.reasoning_level,
            structured_output=p.structured_output,
            streaming=p.streaming,
            fallback_profile_slug=p.fallback_profile_slug,
        )

    settings_row = (await db.execute(select(AISettings).limit(1))).scalar_one_or_none()

    _snapshot = ConfigSnapshot(
        providers=providers,
        profiles=profiles,
        default_profile_slug=settings_row.default_profile_slug if settings_row else None,
        default_provider=settings_row.default_provider if settings_row else None,
        default_model=settings_row.default_model if settings_row else None,
        temperature=settings_row.temperature if settings_row else None,
        max_tokens=settings_row.max_tokens if settings_row else None,
        stage_overrides=dict(settings_row.stage_overrides or {}) if settings_row else {},
        fallback_order=list(settings_row.fallback_order or []) if settings_row else [],
        fallback_enabled=bool(settings_row.fallback_enabled) if settings_row else False,
        loaded=True,
    )
    logger.info(
        "ai_config_snapshot_refreshed providers=%d profiles=%d default_profile=%s fallback=%s",
        len(providers),
        len(profiles),
        _snapshot.default_profile_slug,
        _snapshot.fallback_enabled,
    )
    return _snapshot


async def ensure_loaded(db: AsyncSession) -> ConfigSnapshot:
    """Load the snapshot if it has not been loaded yet."""
    if not _snapshot.loaded:
        return await refresh(db)
    return _snapshot
