"""Tests for AI Profiles, stage mapping, and health classification.

Profiles are the user-facing abstraction, so the behaviour that matters is:
a stage maps to a profile, the profile decides the vendor, and a missing or
looping profile degrades safely instead of breaking every workflow.
"""

from __future__ import annotations

import pytest

from app.ai.config import store
from app.ai.config.resolver import profile_fallback_chain, resolve
from app.ai.config.store import ConfigSnapshot, ProfileRecord, ProviderRecord
from app.ai.config.usage import classify_status
from app.ai.providers.errors import AIProviderAuthError, AIProviderError, AIProviderRateLimitError
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clean_snapshot():
    store.invalidate()
    yield
    store.invalidate()


def _publish(snapshot: ConfigSnapshot) -> None:
    store._snapshot = snapshot  # noqa: SLF001 — test seam


def _settings() -> Settings:
    return Settings(
        ai_provider="gemini",
        gemini_api_key="env-gemini",
        gemini_model="gemini-3.6-flash",
        openai_api_key="env-openai",
        openai_model="gpt-5",
    )


def _profile(slug: str, provider: str, **kw) -> ProfileRecord:
    return ProfileRecord(slug=slug, name=slug.title(), provider_key=provider, **kw)


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


def test_stage_mapped_profile_selects_its_provider():
    _publish(
        ConfigSnapshot(
            profiles={"deep": _profile("deep", "openai", model="gpt-5")},
            stage_overrides={"engineering_review": {"profile": "deep"}},
            loaded=True,
        )
    )
    resolved = resolve(stage="engineering_review", settings=_settings())
    assert resolved.key == "openai"
    assert resolved.model == "gpt-5"
    assert resolved.profile_slug == "deep"
    assert resolved.source == "stage_profile"


def test_unmapped_stage_uses_default_profile():
    _publish(
        ConfigSnapshot(
            profiles={"fast": _profile("fast", "gemini")},
            default_profile_slug="fast",
            loaded=True,
        )
    )
    resolved = resolve(stage="development", settings=_settings())
    assert resolved.profile_slug == "fast"
    assert resolved.source == "default_profile"


def test_shorthand_string_stage_mapping_is_accepted():
    # The UI writes {"planning": "fast"}; the richer dict form is equivalent.
    _publish(
        ConfigSnapshot(
            profiles={"fast": _profile("fast", "gemini")},
            stage_overrides={"planning": "fast"},
            loaded=True,
        )
    )
    assert resolve(stage="planning", settings=_settings()).profile_slug == "fast"


def test_profile_carries_its_own_parameters():
    _publish(
        ConfigSnapshot(
            profiles={"precise": _profile("precise", "openai", temperature=0.05, max_tokens=999)},
            default_profile_slug="precise",
            loaded=True,
        )
    )
    resolved = resolve(settings=_settings())
    assert resolved.config.temperature == 0.05
    assert resolved.config.max_tokens == 999


def test_zero_temperature_is_not_swallowed():
    # 0.0 is falsy; a naive `or` chain would silently replace a deliberate
    # deterministic setting with the default.
    _publish(
        ConfigSnapshot(
            profiles={"det": _profile("det", "openai", temperature=0.0)},
            default_profile_slug="det",
            loaded=True,
        )
    )
    assert resolve(settings=_settings()).config.temperature == 0.0


def test_explicit_provider_beats_profile():
    _publish(
        ConfigSnapshot(
            profiles={"fast": _profile("fast", "gemini")},
            default_profile_slug="fast",
            loaded=True,
        )
    )
    resolved = resolve(provider="openai", settings=_settings())
    assert resolved.key == "openai"
    assert resolved.profile_slug is None


def test_missing_profile_degrades_instead_of_raising():
    # A deleted profile must not break every workflow that referenced it.
    _publish(
        ConfigSnapshot(
            stage_overrides={"planning": {"profile": "gone"}},
            loaded=True,
        )
    )
    resolved = resolve(stage="planning", settings=_settings())
    assert resolved.key == "gemini"  # fell through to environment
    assert resolved.profile_slug is None


def test_profile_uses_provider_key_when_it_declares_no_model():
    _publish(
        ConfigSnapshot(
            providers={
                "openai": ProviderRecord(
                    "openai", "stored", "gpt-5-mini", None, None, None, True, "ready"
                )
            },
            profiles={"p": _profile("p", "openai")},
            default_profile_slug="p",
            loaded=True,
        )
    )
    resolved = resolve(settings=_settings())
    assert resolved.model == "gpt-5-mini"
    assert resolved.config.api_key == "stored"


# ---------------------------------------------------------------------------
# Profile fallback chains
# ---------------------------------------------------------------------------


def test_profile_fallback_chain_is_ordered():
    _publish(
        ConfigSnapshot(
            profiles={
                "a": _profile("a", "gemini", fallback_profile_slug="b"),
                "b": _profile("b", "openai", fallback_profile_slug="c"),
                "c": _profile("c", "groq"),
            },
            loaded=True,
        )
    )
    assert profile_fallback_chain("a") == ["b", "c"]


def test_profile_fallback_cycle_is_broken():
    _publish(
        ConfigSnapshot(
            profiles={
                "a": _profile("a", "gemini", fallback_profile_slug="b"),
                "b": _profile("b", "openai", fallback_profile_slug="a"),
            },
            loaded=True,
        )
    )
    # Must terminate rather than spin between the two.
    assert profile_fallback_chain("a") == ["b"]


def test_profile_fallback_stops_at_missing_link():
    _publish(
        ConfigSnapshot(
            profiles={"a": _profile("a", "gemini", fallback_profile_slug="ghost")},
            loaded=True,
        )
    )
    assert profile_fallback_chain("a") == []


# ---------------------------------------------------------------------------
# Health classification
# ---------------------------------------------------------------------------


def test_status_classification_by_type():
    assert classify_status(None)[0] == "ready"
    assert classify_status(AIProviderRateLimitError("slow down"))[0] == "rate_limited"
    assert classify_status(AIProviderAuthError("nope"))[0] == "auth_failed"


def test_bad_key_reported_as_auth_failure_even_without_401():
    # Gemini answers 400 "API key not valid" rather than 401; reporting that
    # as "offline" would send an operator chasing a network fault.
    err = AIProviderError("API key not valid. Please pass a valid API key.")
    assert classify_status(err)[0] == "auth_failed"


def test_genuine_outage_still_reads_offline():
    assert classify_status(AIProviderError("connection reset by peer"))[0] == "offline"
