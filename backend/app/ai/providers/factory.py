"""Factory for creating LLM provider instances.

This is the seam between callers and provider construction. It kept its
signature when the configuration layer was introduced, so the ~20 call sites
across the agents did not have to change.

What changed underneath: provider selection used to be an if/elif chain over
one process-wide env var. It now delegates to

  app.ai.config.resolver    — decides provider/model/params (stage-aware)
  app.ai.providers.registry — declares providers and builds them

Adding a provider is a `ProviderSpec` in the registry; nothing here changes.
"""

from __future__ import annotations

from app.ai.config.resolver import ResolvedProvider, resolve
from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.registry import (
    UnsupportedProviderError,
    get_provider_spec,
    is_known_model,
)
from app.core.config import Settings
from app.core.exceptions import AppError

__all__ = [
    "SUPPORTED_OPENAI_MODELS",
    "UnsupportedModelError",
    "UnsupportedProviderError",
    "create_llm_provider",
    "validate_resolution",
]

# Retained for backward compatibility: existing imports and tests reference
# this name. The authoritative catalogue now lives in the registry, which the
# API serves to the UI so the model list is no longer duplicated by hand.
SUPPORTED_OPENAI_MODELS = tuple(spec.model_ids() if (spec := get_provider_spec("openai")) else ())


class UnsupportedModelError(AppError):
    """Raised when a request asks for a model the provider does not offer."""

    status_code = 422
    error_code = "unsupported_ai_model"


def create_llm_provider(
    settings: Settings | None = None,
    model: str | None = None,
    provider: str | None = None,
    stage: str | None = None,
) -> ILLMProvider:
    """Instantiate a provider for this request.

    ``model``/``provider`` are per-call overrides and never mutate stored
    configuration. ``stage`` ("planning", "development", ...) lets the
    configuration layer apply a per-stage override when one is configured.

    With nothing configured in the database, resolution falls through to the
    environment variables and behaves exactly as it did before.
    """
    resolved = resolve(provider=provider, model=model, stage=stage, settings=settings)
    validate_resolution(resolved, requested_model=model)
    return resolved.spec.build(resolved.config)


def validate_resolution(resolved: ResolvedProvider, *, requested_model: str | None = None) -> None:
    """Guard a resolution before anything is built or sent.

    Extracted from `create_llm_provider` so the stage-aware execution path
    (`app.agents.llm.StageAwareLLMProvider`, which resolves and sends via
    `app.ai.config.fallback` rather than building here) enforces exactly the
    same rules, with the same exception types and messages, instead of
    re-deriving them. One implementation, two callers — the alternative was
    a second copy of these three checks drifting out of sync with this one.
    """
    if not resolved.spec.implemented:
        raise UnsupportedProviderError(f"{resolved.spec.label} provider is not yet implemented.")

    # Only validate an explicitly requested model. A configured or default
    # model is trusted — an operator who typed a brand-new model ID into
    # settings should not be blocked by our catalogue being a release behind.
    if requested_model is not None and not is_known_model(resolved.key, requested_model):
        raise UnsupportedModelError(f"Unsupported AI model: '{requested_model}'.")

    if resolved.spec.requires_api_key and not resolved.config.api_key:
        # Named after the actual env var (e.g. "OPENAI_API_KEY") rather than
        # just the provider key — this is the message an operator actually
        # acts on, matching the naming Settings/.env.example already use for
        # every api-key-requiring provider.
        env_var = f"{resolved.key.upper()}_API_KEY"
        raise AppError(
            f"No API key configured for provider '{resolved.key}'. Set {env_var}.",
            status_code=503,
            error_code="ai_provider_not_configured",
        )
