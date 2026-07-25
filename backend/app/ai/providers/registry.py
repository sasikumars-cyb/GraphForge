"""Provider registry — the single place a new AI provider is declared.

Why this exists
---------------
Provider *selection* used to be fused into provider *construction*: an
if/elif chain in `factory.py` that read one process-wide env var. Adding a
provider meant editing that function, models were hardcoded per-branch, and
nothing described what a provider could actually do.

This module separates the two. Each provider contributes one `ProviderSpec`
describing its identity, capabilities, models, and how to build it. Adding a
provider is one entry in `_SPECS` — no changes to the factory, the resolver,
the API, or the UI, because all three read the registry rather than hardcoding
provider names.

Capabilities and model catalogues live here (or come from a provider's own
discovery endpoint) so the UI never hardcodes provider-specific knowledge.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.gemini_provider import GeminiProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.core.exceptions import AppError


class Capability(StrEnum):
    """What a provider/model can do. Reported to the UI; never hardcoded there."""

    STREAMING = "streaming"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"
    TOOL_CALLING = "tool_calling"
    REASONING = "reasoning"


class UnsupportedProviderError(AppError):
    """Raised when a provider is declared but has no working adapter yet."""

    status_code = 501
    error_code = "unsupported_ai_provider"


@dataclass(frozen=True)
class ModelSpec:
    """One selectable model."""

    id: str
    label: str = ""
    context_window: int | None = None

    def display(self) -> str:
        return self.label or self.id


@dataclass(frozen=True)
class ProviderBuildConfig:
    """Everything a provider needs to be instantiated.

    Resolved by the configuration layer from stored config, workflow
    overrides, and env defaults — providers never read settings themselves.
    """

    api_key: str | None
    model: str
    temperature: float = 0.2
    max_tokens: int = 4096
    base_url: str | None = None


@dataclass(frozen=True)
class ProviderSpec:
    """Declarative description of one AI provider.

    `build` is the only provider-specific code path. Everything else —
    validation, health, model listing, UI rendering — is driven off these
    fields generically.
    """

    key: str
    label: str
    build: Callable[[ProviderBuildConfig], ILLMProvider]
    capabilities: frozenset[Capability] = frozenset()
    models: tuple[ModelSpec, ...] = ()
    requires_api_key: bool = True
    default_base_url: str | None = None
    default_model: str = ""
    # Optional dynamic model discovery. When a provider exposes a models
    # endpoint the UI prefers it over the static catalogue above.
    discover_models: Callable[[ProviderBuildConfig], Sequence[ModelSpec]] | None = None
    # Declared-but-unimplemented providers still appear in the UI (so users
    # can see what is coming) but refuse to build.
    implemented: bool = True
    notes: str = ""

    def model_ids(self) -> list[str]:
        return [m.id for m in self.models]

    def resolve_default_model(self) -> str:
        if self.default_model:
            return self.default_model
        return self.models[0].id if self.models else ""


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _require_key(spec_key: str, cfg: ProviderBuildConfig) -> str:
    if not cfg.api_key:
        raise AppError(
            f"No API key configured for provider '{spec_key}'.",
            status_code=503,
            error_code="ai_provider_not_configured",
        )
    return cfg.api_key


def _openai_compatible(key: str, url: str) -> Callable[[ProviderBuildConfig], ILLMProvider]:
    """Build an OpenAI-wire-format provider pointed at a different host.

    Groq, Cerebras, OpenRouter and Ollama all speak the OpenAI chat
    completions format, so they need no adapter code of their own — only a
    base URL. This is the payoff of separating selection from construction.
    """

    def _build(cfg: ProviderBuildConfig) -> ILLMProvider:
        return OpenAIProvider(
            api_key=cfg.api_key or "not-required",
            model=cfg.model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            base_url=cfg.base_url or url,
            provider_name=key,
        )

    return _build


def _build_openai(cfg: ProviderBuildConfig) -> ILLMProvider:
    return OpenAIProvider(
        api_key=_require_key("openai", cfg),
        model=cfg.model,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        base_url=cfg.base_url or "https://api.openai.com/v1/chat/completions",
    )


def _build_gemini(cfg: ProviderBuildConfig) -> ILLMProvider:
    return GeminiProvider(
        api_key=_require_key("gemini", cfg),
        model=cfg.model,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
    )


def _unimplemented(label: str) -> Callable[[ProviderBuildConfig], ILLMProvider]:
    def _build(_: ProviderBuildConfig) -> ILLMProvider:
        raise UnsupportedProviderError(f"{label} provider is not yet implemented.")

    return _build


# ---------------------------------------------------------------------------
# The registry — add a provider by adding one ProviderSpec
# ---------------------------------------------------------------------------

_TEXT_CAPS = frozenset({Capability.STRUCTURED_OUTPUT, Capability.TOOL_CALLING})

_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="openai",
        label="OpenAI",
        build=_build_openai,
        capabilities=_TEXT_CAPS | {Capability.STREAMING, Capability.VISION, Capability.REASONING},
        models=(
            ModelSpec("gpt-5.5", "GPT-5.5", 400_000),
            ModelSpec("gpt-5", "GPT-5", 400_000),
            ModelSpec("gpt-5-mini", "GPT-5 mini", 400_000),
            ModelSpec("gpt-4o", "GPT-4o", 128_000),
        ),
        default_model="gpt-5",
        default_base_url="https://api.openai.com/v1/chat/completions",
    ),
    ProviderSpec(
        key="gemini",
        label="Google Gemini",
        build=_build_gemini,
        capabilities=_TEXT_CAPS | {Capability.STREAMING, Capability.VISION, Capability.REASONING},
        models=(
            ModelSpec("gemini-3.6-flash", "Gemini 3.6 Flash", 1_000_000),
            ModelSpec("gemini-2.0-flash", "Gemini 2.0 Flash", 1_000_000),
            ModelSpec("gemini-1.5-pro", "Gemini 1.5 Pro", 2_000_000),
        ),
        default_model="gemini-3.6-flash",
    ),
    ProviderSpec(
        key="groq",
        label="Groq",
        build=_openai_compatible("groq", "https://api.groq.com/openai/v1/chat/completions"),
        capabilities=_TEXT_CAPS | {Capability.STREAMING},
        models=(
            ModelSpec("llama-3.3-70b-versatile", "Llama 3.3 70B", 128_000),
            ModelSpec("llama-3.1-8b-instant", "Llama 3.1 8B Instant", 128_000),
        ),
        default_model="llama-3.3-70b-versatile",
        default_base_url="https://api.groq.com/openai/v1/chat/completions",
    ),
    ProviderSpec(
        key="deepseek",
        label="DeepSeek",
        build=_openai_compatible("deepseek", "https://api.deepseek.com/chat/completions"),
        capabilities=_TEXT_CAPS | {Capability.STREAMING, Capability.REASONING},
        models=(
            ModelSpec("deepseek-v4-pro", "DeepSeek V4 Pro", 128_000),
            ModelSpec("deepseek-chat", "DeepSeek Chat", 128_000),
        ),
        default_model="deepseek-v4-pro",
        default_base_url="https://api.deepseek.com/chat/completions",
        notes="Uses the OpenAI-compatible chat-completions endpoint with a Bearer token.",
    ),
    ProviderSpec(
        key="cerebras",
        label="Cerebras",
        build=_openai_compatible("cerebras", "https://api.cerebras.ai/v1/chat/completions"),
        capabilities=_TEXT_CAPS | {Capability.STREAMING},
        models=(
            ModelSpec("llama-3.3-70b", "Llama 3.3 70B", 128_000),
            ModelSpec("llama3.1-8b", "Llama 3.1 8B", 128_000),
        ),
        default_model="llama-3.3-70b",
        default_base_url="https://api.cerebras.ai/v1/chat/completions",
    ),
    ProviderSpec(
        key="openrouter",
        label="OpenRouter",
        build=_openai_compatible("openrouter", "https://openrouter.ai/api/v1/chat/completions"),
        capabilities=_TEXT_CAPS | {Capability.STREAMING, Capability.VISION},
        models=(
            ModelSpec("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5", 200_000),
            ModelSpec("openai/gpt-5", "GPT-5 (via OpenRouter)", 400_000),
            ModelSpec("google/gemini-2.0-flash-001", "Gemini 2.0 Flash", 1_000_000),
        ),
        default_model="anthropic/claude-sonnet-4.5",
        default_base_url="https://openrouter.ai/api/v1/chat/completions",
        notes="Gateway to many vendors — model IDs are namespaced by upstream provider.",
    ),
    ProviderSpec(
        key="ollama",
        label="Ollama (local)",
        build=_openai_compatible("ollama", "http://localhost:11434/v1/chat/completions"),
        capabilities=_TEXT_CAPS,
        models=(
            ModelSpec("llama3.1", "Llama 3.1"),
            ModelSpec("qwen2.5-coder", "Qwen 2.5 Coder"),
        ),
        default_model="llama3.1",
        requires_api_key=False,
        default_base_url="http://localhost:11434/v1/chat/completions",
        notes="Runs locally; no API key required.",
    ),
    ProviderSpec(
        key="anthropic",
        label="Claude (Anthropic)",
        build=_unimplemented("Claude/Anthropic"),
        capabilities=_TEXT_CAPS | {Capability.STREAMING, Capability.VISION, Capability.REASONING},
        models=(
            ModelSpec("claude-sonnet-4-5", "Claude Sonnet 4.5", 200_000),
            ModelSpec("claude-opus-4-1", "Claude Opus 4.1", 200_000),
        ),
        default_model="claude-sonnet-4-5",
        implemented=False,
        notes="Native adapter pending — usable today via OpenRouter.",
    ),
    ProviderSpec(
        key="azure_openai",
        label="Azure OpenAI",
        build=_unimplemented("Azure OpenAI"),
        capabilities=_TEXT_CAPS | {Capability.STREAMING, Capability.VISION},
        models=(ModelSpec("gpt-4o", "GPT-4o (Azure deployment)", 128_000),),
        implemented=False,
        notes="Requires per-deployment endpoint and api-key header auth.",
    ),
)

_BY_KEY: dict[str, ProviderSpec] = {s.key: s for s in _SPECS}

# Legacy aliases so existing env values keep resolving.
_ALIASES: dict[str, str] = {"claude": "anthropic", "azure": "azure_openai"}


def all_providers() -> tuple[ProviderSpec, ...]:
    """Every declared provider, implemented or not — the UI lists these."""
    return _SPECS


def get_provider_spec(key: str) -> ProviderSpec | None:
    """Look up a provider by key, honouring legacy aliases."""
    normalised = (key or "").strip().lower()
    normalised = _ALIASES.get(normalised, normalised)
    return _BY_KEY.get(normalised)


def require_provider_spec(key: str) -> ProviderSpec:
    spec = get_provider_spec(key)
    if spec is None:
        raise UnsupportedProviderError(f"Unknown AI provider: '{key}'.")
    return spec


def is_known_model(provider_key: str, model: str) -> bool:
    """Whether a model belongs to a provider's catalogue.

    Deliberately permissive for providers whose catalogue is open-ended
    (OpenRouter, Ollama): rejecting unknown IDs there would block valid
    models the moment a vendor ships one.
    """
    spec = get_provider_spec(provider_key)
    if spec is None:
        return False
    if not spec.models or spec.key in ("openrouter", "ollama"):
        return True
    return model in spec.model_ids()
