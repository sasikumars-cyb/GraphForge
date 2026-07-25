"""Provider connection validation.

Verifies credentials, the configured model, and connectivity by issuing the
smallest possible real completion — a one-word reply capped at a handful of
tokens. Cheap enough to run on demand, real enough that a pass means the
provider will actually serve a workflow.

Diagnostics never include the API key, the base URL's query string, or any
part of the credential — only the provider's own error text.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.ai.config.resolver import resolve
from app.ai.config.usage import classify_status
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.ai.providers.registry import require_provider_spec

logger = logging.getLogger(__name__)

# Smallest prompt that still exercises auth, model routing and transport.
_PROBE_SYSTEM = "Reply with the single word: ok"
_PROBE_USER = "ping"


@dataclass(frozen=True)
class ValidationResult:
    provider_key: str
    ok: bool
    status: str
    model: str
    latency_ms: int
    message: str


async def validate_provider(provider_key: str, *, model: str | None = None) -> ValidationResult:
    """Run a minimal live completion against a provider."""
    spec = require_provider_spec(provider_key)

    if not spec.implemented:
        return ValidationResult(
            provider_key=provider_key,
            ok=False,
            status="unavailable",
            model=model or spec.resolve_default_model(),
            latency_ms=0,
            message=f"{spec.label} has no adapter yet.",
        )

    try:
        resolved = resolve(provider=provider_key, model=model)
    except Exception as exc:
        return ValidationResult(
            provider_key=provider_key,
            ok=False,
            status="validation_failed",
            model=model or "",
            latency_ms=0,
            message=str(getattr(exc, "message", exc))[:500],
        )

    if spec.requires_api_key and not resolved.config.api_key:
        return ValidationResult(
            provider_key=provider_key,
            ok=False,
            status="auth_failed",
            model=resolved.model,
            latency_ms=0,
            message="No API key configured.",
        )

    started = time.monotonic()
    try:
        provider = spec.build(resolved.config)
        await provider.complete(
            system_prompt=_PROBE_SYSTEM,
            user_prompt=_PROBE_USER,
            options=LLMRequestOptions(response_format=ResponseFormat.TEXT),
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        return ValidationResult(
            provider_key=provider_key,
            ok=True,
            status="ready",
            model=resolved.model,
            latency_ms=latency_ms,
            message="Connection verified.",
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        status, detail = classify_status(exc)
        logger.info("ai_provider_validation_failed provider=%s status=%s", provider_key, status)
        return ValidationResult(
            provider_key=provider_key,
            ok=False,
            status=status,
            model=resolved.model,
            latency_ms=latency_ms,
            # The provider's own message only — never the credential.
            message=detail or "Validation failed.",
        )
