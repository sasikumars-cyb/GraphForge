"""Factory for creating LLM provider instances.

Supports multiple provider backends. OpenAI, Groq (OpenAI-compatible),
and Gemini are implemented; others raise ``UnsupportedProviderError``
until their adapters are built.
"""

from __future__ import annotations

from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.gemini_provider import GeminiProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError

# The only models the UI is allowed to select (see frontend
# `src/types/aiModel.ts`, which mirrors this list for display). Closed
# vocabulary - never accept an arbitrary model string from a request body.
# Only meaningful when `ai_provider` is "openai" - Groq has its own model
# (`settings.groq_model`), not user-selectable from this UI yet.
SUPPORTED_OPENAI_MODELS = ("gpt-5.5", "gpt-5", "gpt-5-mini")

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class UnsupportedProviderError(AppError):
    """Raised when the configured AI provider is not yet implemented."""

    status_code = 501
    error_code = "unsupported_ai_provider"


class UnsupportedModelError(AppError):
    """Raised when a request asks for a model outside the supported set."""

    status_code = 422
    error_code = "unsupported_ai_model"


def create_llm_provider(settings: Settings | None = None, model: str | None = None) -> ILLMProvider:
    """Instantiate the configured LLM provider.

    Reads ``ai_provider`` from settings and returns the matching concrete
    implementation.  Raises :class:`UnsupportedProviderError` for providers
    that are not yet implemented.

    ``model`` optionally overrides ``settings.openai_model`` for this call
    only (e.g. a per-request model chosen in the UI) - it never mutates
    the process-wide settings object. Must be one of
    :data:`SUPPORTED_OPENAI_MODELS`.
    """
    cfg = settings or get_settings()
    provider_name = cfg.ai_provider.lower()

    if provider_name == "openai":
        if not cfg.openai_api_key:
            raise AppError(
                "OPENAI_API_KEY is not configured.",
                status_code=503,
                error_code="ai_provider_not_configured",
            )
        if model is not None and model not in SUPPORTED_OPENAI_MODELS:
            raise UnsupportedModelError(f"Unsupported AI model: '{model}'.")
        return OpenAIProvider(
            api_key=cfg.openai_api_key,
            model=model or cfg.openai_model,
            temperature=cfg.openai_temperature,
            max_tokens=cfg.openai_max_tokens,
        )

    if provider_name == "groq":
        if not cfg.groq_api_key:
            raise AppError(
                "GROQ_API_KEY is not configured.",
                status_code=503,
                error_code="ai_provider_not_configured",
            )
        return OpenAIProvider(
            api_key=cfg.groq_api_key,
            model=cfg.groq_model,
            temperature=cfg.openai_temperature,
            max_tokens=cfg.openai_max_tokens,
            base_url=_GROQ_CHAT_URL,
            provider_name="groq",
        )

    if provider_name == "gemini":
        if not cfg.gemini_api_key:
            raise AppError(
                "GEMINI_API_KEY is not configured.",
                status_code=503,
                error_code="ai_provider_not_configured",
            )
        return GeminiProvider(
            api_key=cfg.gemini_api_key,
            model=cfg.gemini_model,
            temperature=cfg.openai_temperature,
            max_tokens=cfg.openai_max_tokens,
        )

    if provider_name in ("claude", "anthropic"):
        raise UnsupportedProviderError("Claude/Anthropic provider is not yet implemented.")

    if provider_name == "ollama":
        raise UnsupportedProviderError("Ollama provider is not yet implemented.")

    raise UnsupportedProviderError(f"Unknown AI provider: '{cfg.ai_provider}'.")
