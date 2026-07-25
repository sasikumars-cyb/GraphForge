"""Tests for the AI provider configuration layer.

Covers the three things that make this architecture safe to adopt:

1. Backward compatibility — with nothing configured, resolution matches the
   previous env-var behaviour exactly.
2. Precedence — request > stage override > stored default > environment.
3. Fallback — only recoverable failures fall through, and only when enabled.
"""

from __future__ import annotations

import pytest

from app.ai.config import store
from app.ai.config.fallback import is_recoverable
from app.ai.config.resolver import fallback_chain, resolve
from app.ai.config.store import ConfigSnapshot, ProviderRecord
from app.ai.providers.errors import (
    AIProviderAuthError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
)
from app.ai.providers.factory import create_llm_provider
from app.ai.providers.registry import (
    Capability,
    all_providers,
    get_provider_spec,
    is_known_model,
    require_provider_spec,
)
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clean_snapshot():
    """Every test starts from an unconfigured platform."""
    store.invalidate()
    yield
    store.invalidate()


def _publish(snapshot: ConfigSnapshot) -> None:
    """Install a snapshot without touching the database."""
    store._snapshot = snapshot  # noqa: SLF001 — test seam


def _settings(**overrides) -> Settings:
    base = {
        "ai_provider": "gemini",
        "gemini_api_key": "env-gemini-key",
        "gemini_model": "gemini-3.6-flash",
        "openai_api_key": "env-openai-key",
        "openai_model": "gpt-5",
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_exposes_capabilities_and_models():
    spec = require_provider_spec("openai")
    assert Capability.STRUCTURED_OUTPUT in spec.capabilities
    assert "gpt-5" in spec.model_ids()
    assert spec.resolve_default_model()


def test_registry_resolves_legacy_alias():
    assert get_provider_spec("claude") is get_provider_spec("anthropic")


def test_every_provider_declares_a_default_model():
    # The UI needs something selectable for each provider it renders.
    for spec in all_providers():
        assert spec.resolve_default_model(), f"{spec.key} has no default model"


def test_open_catalogue_providers_accept_unknown_models():
    # OpenRouter/Ollama model IDs change constantly; rejecting unknown ones
    # would block valid models the day a vendor ships them.
    assert is_known_model("openrouter", "some/brand-new-model")
    assert not is_known_model("openai", "definitely-not-a-real-model")


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


def test_unconfigured_platform_falls_back_to_environment():
    resolved = resolve(settings=_settings())
    assert resolved.key == "gemini"
    assert resolved.model == "gemini-3.6-flash"
    assert resolved.config.api_key == "env-gemini-key"
    assert resolved.source == "environment"


def test_gemini_keeps_its_larger_token_budget():
    # Preserves prior behaviour: Gemini's structured JSON needs the bigger
    # budget, and it must not silently inherit the OpenAI one.
    resolved = resolve(settings=_settings(ai_provider="gemini"))
    assert resolved.config.max_tokens == _settings().gemini_max_tokens


def test_factory_still_builds_from_environment_alone():
    provider = create_llm_provider(settings=_settings())
    assert provider is not None


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def test_stored_default_overrides_environment():
    _publish(
        ConfigSnapshot(
            providers={
                "openai": ProviderRecord(
                    "openai", "stored-key", "gpt-5-mini", None, None, None, True, "ready"
                )
            },
            default_provider="openai",
            loaded=True,
        )
    )
    resolved = resolve(settings=_settings())
    assert resolved.key == "openai"
    assert resolved.model == "gpt-5-mini"
    assert resolved.config.api_key == "stored-key"
    assert resolved.source == "stored_default"


def test_stage_override_beats_stored_default():
    _publish(
        ConfigSnapshot(
            providers={
                "openai": ProviderRecord("openai", "k", "gpt-5", None, None, None, True, "ready"),
                "groq": ProviderRecord("groq", "gk", None, None, None, None, True, "ready"),
            },
            default_provider="openai",
            stage_overrides={"planning": {"provider": "groq", "temperature": 0.9}},
            loaded=True,
        )
    )
    planning = resolve(stage="planning", settings=_settings())
    development = resolve(stage="development", settings=_settings())

    assert planning.key == "groq"
    assert planning.source == "stage_override"
    assert planning.config.temperature == 0.9
    # A stage override must not leak into other stages.
    assert development.key == "openai"


def test_explicit_request_beats_everything():
    _publish(ConfigSnapshot(default_provider="openai", loaded=True))
    resolved = resolve(provider="gemini", settings=_settings())
    assert resolved.key == "gemini"
    assert resolved.source == "request"


def test_global_default_model_does_not_leak_across_providers():
    # "gpt-5" configured for OpenAI must never be sent to Gemini.
    _publish(ConfigSnapshot(default_provider="openai", default_model="gpt-5", loaded=True))
    resolved = resolve(provider="gemini", settings=_settings())
    assert resolved.model != "gpt-5"


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc,expected",
    [
        (AIProviderRateLimitError("429"), True),
        (AIProviderTimeoutError("timeout"), True),
        (AIProviderAuthError("bad key"), False),
        (AIProviderResponseError("garbage"), False),
        (ValueError("unrelated"), False),
    ],
)
def test_only_recoverable_failures_fall_through(exc, expected):
    assert is_recoverable(exc) is expected


def test_upstream_5xx_is_recoverable():
    err = AIProviderError("upstream exploded")
    err.status_code = 503
    assert is_recoverable(err) is True


def test_fallback_is_disabled_by_default():
    _publish(
        ConfigSnapshot(
            providers={"groq": ProviderRecord("groq", "k", None, None, None, None, True, "ready")},
            fallback_order=["groq"],
            loaded=True,
        )
    )
    # Configured order but never enabled — must stay empty so a run cannot
    # silently cross vendors.
    assert fallback_chain("gemini") == []


def test_fallback_chain_skips_unusable_providers():
    _publish(
        ConfigSnapshot(
            providers={
                "groq": ProviderRecord("groq", "k", None, None, None, None, True, "ready"),
                "openai": ProviderRecord("openai", "k", None, None, None, None, False, "ready"),
            },
            fallback_order=["gemini", "openai", "anthropic", "groq"],
            fallback_enabled=True,
            loaded=True,
        )
    )
    chain = fallback_chain("gemini")
    assert chain == ["groq"]  # self skipped, disabled skipped, unimplemented skipped
