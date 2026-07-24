"""Provider exception hierarchy used by concrete LLM providers."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import AppError


class AIProviderError(AppError):
    """Base class for AI provider errors."""

    status_code = 502
    error_code = "ai_provider_error"
    provider_error: dict[str, Any] | None = None


class AIProviderTimeoutError(AIProviderError):
    """The provider did not respond within the configured timeout."""

    error_code = "ai_provider_timeout"


class AIProviderAuthError(AIProviderError):
    """The API key is invalid or missing."""

    status_code = 401
    error_code = "ai_provider_auth_error"


class AIProviderRateLimitError(AIProviderError):
    """The provider rate-limited the request."""

    status_code = 429
    error_code = "ai_provider_rate_limit"


class AIProviderResponseError(AIProviderError):
    """The provider returned a malformed or unparseable response."""

    error_code = "ai_provider_response_error"
