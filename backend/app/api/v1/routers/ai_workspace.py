"""AI Workspace API — providers, profiles, defaults, mapping, health, usage.

This router is the only way the UI touches AI configuration. It reads
provider metadata from the Provider Registry rather than defining its own, so
adding a provider needs one `ProviderSpec` and zero changes here or in the
frontend.

Security: API keys are write-only. No response model in
`app.schemas.ai_workspace` has a field capable of carrying one.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.config import store
from app.ai.config.resolver import resolve
from app.ai.config.usage import all_usage
from app.ai.config.validation import validate_provider
from app.ai.providers.registry import (
    ProviderSpec,
    all_providers,
    get_provider_spec,
    require_provider_spec,
)
from app.api.v1.dependencies import require_admin
from app.core.crypto import encrypt_secret
from app.core.exceptions import AppError, NotFoundError
from app.database.session import get_db_session
from app.models.ai_profile import AIProfile, AIProviderUsage
from app.models.ai_provider_config import AIProviderConfig, AISettings
from app.models.user import User
from app.schemas.ai_workspace import (
    AIWorkspaceOverview,
    AIWorkspaceSettings,
    AIWorkspaceSettingsUpdate,
    ModelInfo,
    ProfileInfo,
    ProfileUpsertRequest,
    ProviderInfo,
    ProviderUpsertRequest,
    ProviderUsage,
    StageMapping,
    ValidationResponse,
)
from app.services import workflow_service

router = APIRouter(prefix="/ai", tags=["ai-workspace"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "profile"


async def _settings_row(db: AsyncSession) -> AISettings:
    """The singleton settings row, created on first use."""
    row = (await db.execute(select(AISettings).limit(1))).scalar_one_or_none()
    if row is None:
        row = AISettings()
        db.add(row)
        await db.flush()
    return row


async def _reload(db: AsyncSession) -> None:
    """Publish a fresh snapshot so changes apply without a restart."""
    await db.commit()
    await store.refresh(db)


def _build_provider_info(spec: ProviderSpec, config: AIProviderConfig | None) -> ProviderInfo:
    return ProviderInfo(
        key=spec.key,
        label=spec.label,
        implemented=spec.implemented,
        notes=spec.notes,
        capabilities=sorted(c.value for c in spec.capabilities),
        models=[
            ModelInfo(id=m.id, label=m.display(), context_window=m.context_window)
            for m in spec.models
        ],
        requires_api_key=spec.requires_api_key,
        default_model=spec.resolve_default_model(),
        configured=config is not None,
        enabled=config.enabled if config else True,
        api_key_configured=bool(config and config.encrypted_api_key),
        model=config.model if config else None,
        provider_options=dict(config.provider_options) if config and config.provider_options else {},
        base_url=config.base_url if config else None,
        temperature=config.temperature if config else None,
        max_tokens=config.max_tokens if config else None,
        status=config.status if config else "unknown",
        status_detail=config.status_detail if config else None,
        last_validated_at=config.last_validated_at if config else None,
        last_success_at=config.last_success_at if config else None,
        latency_ms=config.latency_ms if config else None,
    )


def _build_profile_info(profile: AIProfile, configs: dict[str, AIProviderConfig]) -> ProfileInfo:
    spec = get_provider_spec(profile.provider_key)
    config = configs.get(profile.provider_key)
    try:
        effective = resolve(profile=profile.slug)
        effective_model = effective.model
    except Exception:
        effective_model = profile.model or (spec.resolve_default_model() if spec else "")
    return ProfileInfo(
        slug=profile.slug,
        name=profile.name,
        description=profile.description,
        provider_key=profile.provider_key,
        provider_label=spec.label if spec else profile.provider_key,
        model=profile.model,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        reasoning_level=profile.reasoning_level,
        structured_output=profile.structured_output,
        streaming=profile.streaming,
        fallback_profile_slug=profile.fallback_profile_slug,
        is_system=profile.is_system,
        effective_model=effective_model,
        provider_status=config.status if config else "unknown",
    )


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("/workspace", response_model=AIWorkspaceOverview, summary="AI Workspace overview")
async def workspace_overview(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> AIWorkspaceOverview:
    """Everything the AI Workspace needs, in one round trip."""
    await store.refresh(db)
    snapshot = store.current_snapshot()

    configs = {
        c.provider_key: c for c in (await db.execute(select(AIProviderConfig))).scalars().all()
    }
    providers = [_build_provider_info(spec, configs.get(spec.key)) for spec in all_providers()]

    profile_rows = (await db.execute(select(AIProfile))).scalars().all()
    profiles = [_build_profile_info(p, configs) for p in profile_rows]

    settings_row = (await db.execute(select(AISettings).limit(1))).scalar_one_or_none()
    settings = AIWorkspaceSettings(
        default_profile_slug=settings_row.default_profile_slug if settings_row else None,
        default_provider=settings_row.default_provider if settings_row else None,
        default_model=settings_row.default_model if settings_row else None,
        temperature=settings_row.temperature if settings_row else None,
        max_tokens=settings_row.max_tokens if settings_row else None,
        stage_overrides=dict(settings_row.stage_overrides or {}) if settings_row else {},
        fallback_order=list(settings_row.fallback_order or []) if settings_row else [],
        fallback_enabled=bool(settings_row.fallback_enabled) if settings_row else False,
    )

    # Stage list comes from the workflow service, so a new stage appears here
    # automatically rather than being duplicated in this router.
    stages: list[StageMapping] = []
    for stage_key in workflow_service.STAGE_GOALS:
        slug = snapshot.stage_profile(stage_key)
        profile_record = snapshot.profile(slug) if slug else None
        try:
            resolved = resolve(stage=stage_key)
            effective_provider, effective_model, source = (
                resolved.key,
                resolved.model,
                resolved.source,
            )
        except Exception:
            effective_provider, effective_model, source = "", "", "unresolved"
        stages.append(
            StageMapping(
                stage=stage_key,
                label=workflow_service.STAGE_LABELS.get(stage_key, stage_key),
                profile_slug=slug,
                profile_name=profile_record.name if profile_record else None,
                effective_provider=effective_provider,
                effective_model=effective_model,
                source=source,
            )
        )

    usage_rows = await all_usage(db)
    usage = [_usage_dto(u) for u in usage_rows.values()]

    return AIWorkspaceOverview(
        providers=providers, profiles=profiles, settings=settings, stages=stages, usage=usage
    )


def _usage_dto(row: AIProviderUsage) -> ProviderUsage:
    return ProviderUsage(
        provider_key=row.provider_key,
        requests=row.requests,
        successes=row.successes,
        failures=row.failures,
        rate_limit_events=row.rate_limit_events,
        auth_failures=row.auth_failures,
        # Averaged over successes only — failed requests have no meaningful
        # latency to average in.
        average_latency_ms=(int(row.total_latency_ms / row.successes) if row.successes else None),
        last_request_at=row.last_request_at,
        last_success_at=row.last_success_at,
        last_failure_at=row.last_failure_at,
        last_rate_limit_at=row.last_rate_limit_at,
        last_error=row.last_error,
    )


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


@router.get("/providers", response_model=list[ProviderInfo], summary="List providers")
async def list_providers(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> list[ProviderInfo]:
    configs = {
        c.provider_key: c for c in (await db.execute(select(AIProviderConfig))).scalars().all()
    }
    return [_build_provider_info(spec, configs.get(spec.key)) for spec in all_providers()]


@router.get(
    "/providers/{provider_key}/models",
    response_model=list[ModelInfo],
    summary="List a provider's models",
)
async def list_models(
    provider_key: str,
    _: User = Depends(require_admin),
) -> list[ModelInfo]:
    """Models for a provider — discovered dynamically when the provider
    supports it, otherwise from the registry catalogue."""
    spec = require_provider_spec(provider_key)
    if spec.discover_models is not None:
        try:
            resolved = resolve(provider=provider_key)
            return [
                ModelInfo(id=m.id, label=m.display(), context_window=m.context_window)
                for m in spec.discover_models(resolved.config)
            ]
        except Exception:
            # Discovery is a convenience; the static catalogue is the contract.
            pass
    return [
        ModelInfo(id=m.id, label=m.display(), context_window=m.context_window) for m in spec.models
    ]


@router.put(
    "/providers/{provider_key}", response_model=ProviderInfo, summary="Configure a provider"
)
async def upsert_provider(
    provider_key: str,
    body: ProviderUpsertRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> ProviderInfo:
    spec = require_provider_spec(provider_key)

    row = (
        await db.execute(select(AIProviderConfig).where(AIProviderConfig.provider_key == spec.key))
    ).scalar_one_or_none()
    if row is None:
        row = AIProviderConfig(provider_key=spec.key)
        db.add(row)

    # Omitted key -> keep what is stored. Empty string -> clear it. This lets
    # the UI submit the form without ever round-tripping the secret.
    if body.api_key is not None:
        row.encrypted_api_key = encrypt_secret(body.api_key) if body.api_key else None
        # Credentials changed: the old health verdict no longer applies.
        row.status = "unknown"
        row.status_detail = None

    for field in ("model", "base_url", "temperature", "max_tokens", "enabled", "provider_options"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)

    await _reload(db)
    return _build_provider_info(spec, row)


@router.delete(
    "/providers/{provider_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove provider configuration",
)
async def delete_provider(
    provider_key: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    row = (
        await db.execute(
            select(AIProviderConfig).where(AIProviderConfig.provider_key == provider_key)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Provider '{provider_key}' is not configured.")
    await db.delete(row)
    await _reload(db)


@router.post(
    "/providers/{provider_key}/validate",
    response_model=ValidationResponse,
    summary="Validate a provider connection",
)
async def validate(
    provider_key: str,
    model: str | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> ValidationResponse:
    """Verify credentials, model and connectivity with a minimal live call."""
    await store.ensure_loaded(db)
    result = await validate_provider(provider_key, model=model)

    row = (
        await db.execute(
            select(AIProviderConfig).where(AIProviderConfig.provider_key == provider_key)
        )
    ).scalar_one_or_none()
    if row is not None:
        from datetime import UTC, datetime

        row.status = result.status
        row.status_detail = result.message
        row.last_validated_at = datetime.now(UTC)
        if result.ok:
            row.latency_ms = result.latency_ms
        await db.commit()

    return ValidationResponse(**result.__dict__)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@router.get("/profiles", response_model=list[ProfileInfo], summary="List AI profiles")
async def list_profiles(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> list[ProfileInfo]:
    await store.ensure_loaded(db)
    configs = {
        c.provider_key: c for c in (await db.execute(select(AIProviderConfig))).scalars().all()
    }
    rows = (await db.execute(select(AIProfile))).scalars().all()
    return [_build_profile_info(p, configs) for p in rows]


@router.post(
    "/profiles",
    response_model=ProfileInfo,
    status_code=status.HTTP_201_CREATED,
    summary="Create an AI profile",
)
async def create_profile(
    body: ProfileUpsertRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> ProfileInfo:
    require_provider_spec(body.provider_key)

    slug = _slugify(body.name)
    existing = (
        await db.execute(select(AIProfile).where(AIProfile.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            f"A profile named '{body.name}' already exists.",
            status_code=409,
            error_code="ai_profile_exists",
        )

    row = AIProfile(slug=slug, **body.model_dump(exclude={"name"}), name=body.name)
    db.add(row)
    await _reload(db)

    configs = {
        c.provider_key: c for c in (await db.execute(select(AIProviderConfig))).scalars().all()
    }
    return _build_profile_info(row, configs)


@router.put("/profiles/{slug}", response_model=ProfileInfo, summary="Update an AI profile")
async def update_profile(
    slug: str,
    body: ProfileUpsertRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> ProfileInfo:
    require_provider_spec(body.provider_key)
    row = (await db.execute(select(AIProfile).where(AIProfile.slug == slug))).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Profile '{slug}' not found.")

    # The slug is intentionally immutable: stage mappings and fallback chains
    # reference it, and renaming would silently break them.
    for field, value in body.model_dump().items():
        setattr(row, field, value)

    await _reload(db)
    configs = {
        c.provider_key: c for c in (await db.execute(select(AIProviderConfig))).scalars().all()
    }
    return _build_profile_info(row, configs)


@router.delete(
    "/profiles/{slug}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an AI profile"
)
async def delete_profile(
    slug: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    row = (await db.execute(select(AIProfile).where(AIProfile.slug == slug))).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Profile '{slug}' not found.")

    # Refuse to orphan references rather than silently degrading every stage
    # that points here.
    settings_row = (await db.execute(select(AISettings).limit(1))).scalar_one_or_none()
    if settings_row is not None:
        referencing = [
            stage
            for stage, entry in (settings_row.stage_overrides or {}).items()
            if (isinstance(entry, dict) and entry.get("profile") == slug) or entry == slug
        ]
        if referencing:
            raise AppError(
                f"Profile '{slug}' is mapped to: {', '.join(referencing)}. "
                "Remap those stages first.",
                status_code=409,
                error_code="ai_profile_in_use",
            )
        if settings_row.default_profile_slug == slug:
            raise AppError(
                f"Profile '{slug}' is the default profile. Choose another default first.",
                status_code=409,
                error_code="ai_profile_in_use",
            )

    await db.delete(row)
    await _reload(db)


# ---------------------------------------------------------------------------
# Settings — defaults, workflow mapping, fallback
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=AIWorkspaceSettings, summary="Get AI defaults")
async def get_settings_endpoint(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> AIWorkspaceSettings:
    row = await _settings_row(db)
    await db.commit()
    return AIWorkspaceSettings(
        default_profile_slug=row.default_profile_slug,
        default_provider=row.default_provider,
        default_model=row.default_model,
        temperature=row.temperature,
        max_tokens=row.max_tokens,
        stage_overrides=dict(row.stage_overrides or {}),
        fallback_order=list(row.fallback_order or []),
        fallback_enabled=bool(row.fallback_enabled),
    )


@router.put("/settings", response_model=AIWorkspaceSettings, summary="Update AI defaults")
async def update_settings_endpoint(
    body: AIWorkspaceSettingsUpdate,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> AIWorkspaceSettings:
    row = await _settings_row(db)

    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, field, value)

    # Validate references so a typo cannot silently disable every workflow.
    if row.default_profile_slug:
        exists = (
            await db.execute(select(AIProfile).where(AIProfile.slug == row.default_profile_slug))
        ).scalar_one_or_none()
        if exists is None:
            raise NotFoundError(f"Profile '{row.default_profile_slug}' not found.")
    if row.default_provider:
        require_provider_spec(row.default_provider)
    for key in row.fallback_order or []:
        require_provider_spec(key)

    await _reload(db)
    return AIWorkspaceSettings(
        default_profile_slug=row.default_profile_slug,
        default_provider=row.default_provider,
        default_model=row.default_model,
        temperature=row.temperature,
        max_tokens=row.max_tokens,
        stage_overrides=dict(row.stage_overrides or {}),
        fallback_order=list(row.fallback_order or []),
        fallback_enabled=bool(row.fallback_enabled),
    )


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


@router.get("/usage", response_model=list[ProviderUsage], summary="Provider usage")
async def get_usage(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> list[ProviderUsage]:
    """Observed usage only. Quota is never estimated — if a vendor does not
    report it, it is simply absent."""
    rows = await all_usage(db)
    return [_usage_dto(r) for r in rows.values()]
