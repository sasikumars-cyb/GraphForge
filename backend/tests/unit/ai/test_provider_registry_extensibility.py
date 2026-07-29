"""Regression tests for Part 3/11/12: provider registration is entirely
declarative, and provider_options is the structured home for provider-
specific settings (replacing the base_url-holds-Bedrock-region hack).
"""

from __future__ import annotations

from app.ai.config.resolver import _resolve_provider_options
from app.ai.config.store import ProviderRecord
from app.ai.providers.registry import (
    ProviderBuildConfig,
    all_providers,
    get_provider_spec,
    is_known_model,
)
from app.core.config import Settings


def test_every_provider_declares_implemented_status():
    for spec in all_providers():
        assert isinstance(spec.implemented, bool)


def test_vertex_ai_is_declared_but_not_implemented():
    spec = get_provider_spec("vertex_ai")
    assert spec is not None
    assert spec.implemented is False
    assert spec.key == "vertex_ai"


def test_unimplemented_provider_build_raises_on_use():
    from app.ai.providers.registry import UnsupportedProviderError

    spec = get_provider_spec("vertex_ai")
    cfg = ProviderBuildConfig(api_key=None, model="gemini-3.6-pro")
    try:
        spec.build(cfg)
        raise AssertionError("expected UnsupportedProviderError")
    except UnsupportedProviderError:
        pass


def test_openai_compatible_providers_need_no_adapter_code():
    """Groq/Cerebras/OpenRouter/Ollama all build via the same
    _openai_compatible() factory — adding another OpenAI-wire-format
    provider is a registry entry only."""
    for key in ("groq", "cerebras", "openrouter", "ollama"):
        spec = get_provider_spec(key)
        assert spec is not None
        assert spec.implemented is True


def test_bedrock_provider_options_take_precedence_over_legacy_base_url():
    """New rows (with provider_options populated) must win over the old
    base_url-as-region hack for the same row."""
    record = ProviderRecord(
        provider_key="bedrock",
        api_key=None,
        model="us.anthropic.claude-sonnet-4-20250514",
        base_url="us-west-2",  # stale legacy value, must be ignored
        temperature=None,
        max_tokens=None,
        enabled=True,
        status="unknown",
        provider_options={"region": "eu-central-1"},
    )
    options = _resolve_provider_options("bedrock", record, Settings())
    assert options["region"] == "eu-central-1"


def test_bedrock_legacy_base_url_still_resolves_when_no_provider_options():
    """Backward compatibility: a row saved before provider_options existed
    still resolves its region from base_url."""
    record = ProviderRecord(
        provider_key="bedrock",
        api_key=None,
        model="us.anthropic.claude-sonnet-4-20250514",
        base_url="ap-south-1",
        temperature=None,
        max_tokens=None,
        enabled=True,
        status="unknown",
        provider_options={},
    )
    options = _resolve_provider_options("bedrock", record, Settings())
    assert options["region"] == "ap-south-1"


def test_provider_options_defaults_to_empty_dict():
    record = ProviderRecord(
        provider_key="openai",
        api_key="k",
        model="gpt-5",
        base_url=None,
        temperature=None,
        max_tokens=None,
        enabled=True,
        status="unknown",
    )
    assert record.provider_options == {}


def test_is_known_model_permissive_for_open_ended_catalogues():
    assert is_known_model("openrouter", "anything/not-in-catalogue") is True
    assert is_known_model("openai", "not-a-real-model") is False
