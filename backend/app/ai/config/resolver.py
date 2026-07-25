"""The AI configuration layer.

Resolves *what to run* — provider, model, generation parameters — before any
provider is constructed. Callers ask for a stage ("planning") or a profile
("fast-planner"), not a vendor; which vendor serves that intent is
configuration, not code.

Precedence, most specific wins:

    explicit call argument       (per-request profile/provider/model)
      -> stage profile mapping   (stage_overrides["planning"]["profile"])
      -> stage provider override (stage_overrides["planning"]["provider"])
      -> stored default profile  (ai_settings.default_profile_slug)
      -> stored global default   (ai_settings.default_provider/model)
      -> environment variables   (legacy Settings — backward compatibility)

The env layer is last, never removed. An installation that has configured
nothing in the UI resolves exactly as it did before this layer existed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.ai.config.store import ConfigSnapshot, current_snapshot
from app.ai.providers.registry import (
    ProviderBuildConfig,
    ProviderSpec,
    get_provider_spec,
    require_provider_spec,
)
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Env fields that carry a provider's key/model, kept out of the registry so
# provider specs stay free of settings coupling. Legacy only — new providers
# are configured through the UI and need no entry here.
_ENV_KEY_FIELDS: dict[str, tuple[str, str]] = {
    "openai": ("openai_api_key", "openai_model"),
    "gemini": ("gemini_api_key", "gemini_model"),
    "groq": ("groq_api_key", "groq_model"),
}


@dataclass(frozen=True)
class ResolvedProvider:
    """A fully-resolved decision about how to serve one request."""

    spec: ProviderSpec
    config: ProviderBuildConfig
    # request | stage_profile | stage_override | default_profile |
    # stored_default | environment
    source: str
    # Set when a profile drove the decision, so callers and the UI can report
    # "served by Fast Planner" rather than leaking the vendor name.
    profile_slug: str | None = None
    profile_name: str | None = None

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def model(self) -> str:
        return self.config.model


def _env_credentials(spec_key: str, settings: Settings) -> tuple[str | None, str | None]:
    fields = _ENV_KEY_FIELDS.get(spec_key)
    if not fields:
        return None, None
    key_field, model_field = fields
    return getattr(settings, key_field, None), getattr(settings, model_field, None)


def _pick_provider_key(
    snapshot: ConfigSnapshot,
    settings: Settings,
    provider: str | None,
    stage: str | None,
) -> tuple[str, str]:
    """Choose a provider key and record where the decision came from."""
    if provider:
        return provider, "request"

    if stage:
        override = snapshot.stage_overrides.get(stage) or {}
        if isinstance(override, dict) and override.get("provider"):
            return str(override["provider"]), "stage_override"

    if snapshot.default_provider:
        return snapshot.default_provider, "stored_default"

    return settings.ai_provider, "environment"


def _pick_model(
    spec: ProviderSpec,
    snapshot: ConfigSnapshot,
    settings: Settings,
    model: str | None,
    stage: str | None,
) -> str:
    if model:
        return model

    if stage:
        override = snapshot.stage_overrides.get(stage) or {}
        if isinstance(override, dict) and override.get("model"):
            return str(override["model"])

    record = snapshot.provider(spec.key)
    if record and record.model:
        return record.model

    # A stored global default model only applies to the provider it was
    # chosen for — carrying "gpt-5" onto Gemini would produce a guaranteed
    # 404 at request time.
    if snapshot.default_model and snapshot.default_provider == spec.key:
        return snapshot.default_model

    _, env_model = _env_credentials(spec.key, settings)
    if env_model:
        return env_model

    return spec.resolve_default_model()


def _stage_number(snapshot: ConfigSnapshot, stage: str | None, field: str) -> float | None:
    if not stage:
        return None
    override = snapshot.stage_overrides.get(stage) or {}
    if isinstance(override, dict) and override.get(field) is not None:
        return float(override[field])
    return None


def _select_profile(
    snapshot: ConfigSnapshot, profile: str | None, stage: str | None
) -> tuple[str | None, str]:
    """Choose which profile (if any) governs this request."""
    if profile:
        return profile, "request"
    stage_slug = snapshot.stage_profile(stage)
    if stage_slug:
        return stage_slug, "stage_profile"
    if snapshot.default_profile_slug:
        return snapshot.default_profile_slug, "default_profile"
    return None, ""


def resolve(
    *,
    provider: str | None = None,
    model: str | None = None,
    stage: str | None = None,
    profile: str | None = None,
    settings: Settings | None = None,
) -> ResolvedProvider:
    """Resolve a provider decision. Pure and synchronous — reads the snapshot.

    A profile, when one applies, supplies the provider/model/parameters. An
    explicit `provider` or `model` argument still wins over it: a caller that
    named a vendor asked for that vendor.
    """
    cfg = settings or get_settings()
    snapshot = current_snapshot()

    # ── Profile-first resolution ────────────────────────────────────
    if not provider:
        slug, profile_source = _select_profile(snapshot, profile, stage)
        record = snapshot.profile(slug) if slug else None
        if record is not None:
            spec = require_provider_spec(record.provider_key)
            prov_cfg = snapshot.provider(spec.key)
            env_key, _ = _env_credentials(spec.key, cfg)
            resolved_model = (
                model
                or record.model
                or (prov_cfg.model if prov_cfg else None)
                or (_env_credentials(spec.key, cfg)[1] or "")
                or spec.resolve_default_model()
            )
            return ResolvedProvider(
                spec=spec,
                config=ProviderBuildConfig(
                    api_key=(prov_cfg.api_key if prov_cfg else None) or env_key,
                    model=resolved_model,
                    temperature=float(
                        record.temperature
                        if record.temperature is not None
                        else (
                            prov_cfg.temperature
                            if prov_cfg and prov_cfg.temperature is not None
                            else (
                                snapshot.temperature
                                if snapshot.temperature is not None
                                else cfg.openai_temperature
                            )
                        )
                    ),
                    max_tokens=int(
                        record.max_tokens
                        or (prov_cfg.max_tokens if prov_cfg else None)
                        or snapshot.max_tokens
                        or (
                            cfg.gemini_max_tokens if spec.key == "gemini" else cfg.openai_max_tokens
                        )
                    ),
                    base_url=(prov_cfg.base_url if prov_cfg else None) or spec.default_base_url,
                ),
                source=profile_source,
                profile_slug=record.slug,
                profile_name=record.name,
            )
        if slug:
            # A stage or default points at a profile that no longer exists.
            # Fall through to provider-level resolution rather than failing the
            # run — a deleted profile should degrade, not break every workflow.
            logger.warning("ai_profile_missing slug=%s stage=%s", slug, stage)

    provider_key, source = _pick_provider_key(snapshot, cfg, provider, stage)
    spec = require_provider_spec(provider_key)
    record = snapshot.provider(spec.key)

    resolved_model = _pick_model(spec, snapshot, cfg, model, stage)

    env_key, _ = _env_credentials(spec.key, cfg)
    api_key = (record.api_key if record else None) or env_key

    temperature = (
        _stage_number(snapshot, stage, "temperature")
        or (record.temperature if record else None)
        or snapshot.temperature
        or cfg.openai_temperature
    )
    max_tokens = (
        _stage_number(snapshot, stage, "max_tokens")
        or (record.max_tokens if record else None)
        or snapshot.max_tokens
        # Gemini's structured JSON responses need the larger budget; keeping
        # this per-provider preserves existing behaviour exactly.
        or (cfg.gemini_max_tokens if spec.key == "gemini" else cfg.openai_max_tokens)
    )

    return ResolvedProvider(
        spec=spec,
        config=ProviderBuildConfig(
            api_key=api_key,
            model=resolved_model,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            base_url=(record.base_url if record else None) or spec.default_base_url,
        ),
        source=source,
    )


def profile_fallback_chain(slug: str, max_depth: int = 4) -> list[str]:
    """Follow a profile's `fallback_profile_slug` links into an ordered chain.

    Cycle-protected and depth-capped: a profile pair that points at each other
    must not spin, and an accidental long chain must not fan out into a dozen
    vendor calls on one failure.
    """
    snapshot = current_snapshot()
    chain: list[str] = []
    seen = {slug}
    current = snapshot.profile(slug)
    while current and current.fallback_profile_slug and len(chain) < max_depth:
        nxt = current.fallback_profile_slug
        if nxt in seen:
            logger.warning("ai_profile_fallback_cycle slug=%s", nxt)
            break
        seen.add(nxt)
        record = snapshot.profile(nxt)
        if record is None:
            logger.warning("ai_profile_fallback_missing slug=%s", nxt)
            break
        chain.append(nxt)
        current = record
    return chain


def fallback_chain(primary: str, settings: Settings | None = None) -> list[str]:
    """Provider keys to try after a recoverable failure of `primary`.

    Empty unless an operator explicitly enabled fallback — a run must never
    silently cross vendors because someone happened to configure two keys.
    """
    snapshot = current_snapshot()
    if not snapshot.fallback_enabled:
        return []
    chain: list[str] = []
    for key in snapshot.fallback_order:
        if key == primary or key in chain:
            continue
        record = snapshot.provider(key)
        spec = get_provider_spec(key)
        if spec is None or not spec.implemented:
            continue
        if record is None or not record.enabled:
            continue
        chain.append(key)
    return chain
