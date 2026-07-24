"""Shared HTTP + error handling helpers for AI providers."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.ai.providers.base import ProviderErrorMetadata
from app.ai.providers.errors import AIProviderAuthError, AIProviderError, AIProviderRateLimitError

logger = logging.getLogger(__name__)


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def extract_provider_error_metadata(
    response: httpx.Response,
    *,
    provider: str,
    model: str,
) -> ProviderErrorMetadata:
    """Extract structured metadata from OpenAI-compatible or Gemini errors."""
    message: str | None = None
    error_type: str | None = None
    error_code: str | None = None

    try:
        body = response.json()
    except Exception:
        body = None

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = _as_str(error.get("message"))
            # OpenAI-compatible providers use error.type / error.code.
            # Gemini returns error.status / error.code.
            error_type = _as_str(error.get("type")) or _as_str(error.get("status"))
            error_code = _as_str(error.get("code"))

    return ProviderErrorMetadata(
        provider=provider,
        model=model,
        status_code=response.status_code,
        retry_after=response.headers.get("retry-after"),
        error_message=message,
        error_type=error_type,
        error_code=error_code,
    )


def raise_for_error_response(
    response: httpx.Response,
    *,
    provider: str,
    model: str,
) -> None:
    """Raise provider-specific exceptions with structured metadata.

    Public API stays backward-compatible (same exception classes and
    HTTP statuses), while metadata is attached internally.
    """
    if response.status_code < 400:
        return

    meta = extract_provider_error_metadata(response, provider=provider, model=model)

    logger.warning(
        "llm_provider_error provider=%s model=%s status_code=%s retry_after=%s "
        "error_type=%s error_code=%s error_message=%r",
        meta.provider,
        meta.model,
        meta.status_code,
        meta.retry_after,
        meta.error_type,
        meta.error_code,
        meta.error_message,
    )

    status_code = response.status_code
    if status_code in {401, 403}:
        message = meta.error_message or "Invalid or missing API key."
        exc: AIProviderError = AIProviderAuthError(message)
    elif status_code == 429:
        message = meta.error_message or "AI provider rate limit exceeded."
        exc = AIProviderRateLimitError(message)
    else:
        message = meta.error_message or f"AI provider returned status {status_code}."
        exc = AIProviderError(message)

    exc.provider_error = meta.as_dict()
    raise exc
