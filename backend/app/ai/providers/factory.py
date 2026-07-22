"""Factory for creating LLM provider instances.

Supports multiple provider backends.  Only OpenAI is currently implemented;
others raise ``NotImplementedError`` until their adapters are built.
"""

from __future__ import annotations

from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError


class UnsupportedProviderError(AppError):
    """Raised when the configured AI provider is not yet implemented."""

    status_code = 501
    error_code = "unsupported_ai_provider"


def create_llm_provider(settings: Settings | None = None) -> ILLMProvider:
    """Instantiate the configured LLM provider.

    Reads ``ai_provider`` from settings and returns the matching concrete
    implementation.  Raises :class:`UnsupportedProviderError` for providers
    that are not yet implemented.
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
        return OpenAIProvider(
            api_key=cfg.openai_api_key,
            model=cfg.openai_model,
            temperature=cfg.openai_temperature,
            max_tokens=cfg.openai_max_tokens,
        )

    if provider_name in ("claude", "anthropic"):
        raise UnsupportedProviderError("Claude/Anthropic provider is not yet implemented.")

    if provider_name == "gemini":
        raise UnsupportedProviderError("Gemini provider is not yet implemented.")

    if provider_name == "ollama":
        raise UnsupportedProviderError("Ollama provider is not yet implemented.")

    raise UnsupportedProviderError(f"Unknown AI provider: '{cfg.ai_provider}'.")
