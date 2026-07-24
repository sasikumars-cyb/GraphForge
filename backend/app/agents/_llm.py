"""Shared LLM call + prompt rendering mechanics for the freeform-JSON agents
(Planning, Development, Testing).

Not part of the IAgent framework and not a new abstraction layer — this is
the one HTTP-call implementation that Planning/Development/Testing's
`_call_llm` were each duplicating byte-for-byte (only the exception class
and system prompt differed). Extracted here so there is exactly one place
that knows how to talk to the configured provider; each agent keeps its
own `*LLMError` subclass (distinct `error_code`, an observable part of the
API's error responses) and its own thin `_call_llm` wrapper so existing
`patch("app.agents.<x>.agent._call_llm", ...)` test seams keep working.

The Review Agent is intentionally not part of this: it goes through the
existing `ILLMProvider` / `create_llm_provider()` port (app/ai/providers),
which is schema-bound to `AIAnalysisResult` and out of scope here.
"""

from __future__ import annotations

import logging
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any, NoReturn

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


def render_prompt_template(
    template_path: Path, task_description: str, graph_context: str, max_graph_context_chars: int
) -> str:
    """Strip YAML front-matter and substitute the two template variables
    every freeform-JSON agent prompt uses."""
    raw = template_path.read_text(encoding="utf-8")
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", raw, flags=re.DOTALL)
    body = body.replace("{{ task_description }}", task_description)
    body = body.replace("{{ graph_context }}", graph_context[:max_graph_context_chars])
    return body


def _extract_provider_error_metadata(
    response: httpx.Response,
    *,
    provider: str,
    model: str,
) -> dict[str, Any]:
    """Extract provider error metadata from OpenAI-compatible error JSON.

    Parses the response body at most once and returns a normalized metadata
    dict that can be logged and attached to raised exceptions.
    """
    metadata: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "status_code": response.status_code,
        "retry_after": response.headers.get("retry-after"),
        "error_message": None,
        "error_type": None,
        "error_code": None,
    }

    try:
        body = response.json()
    except (JSONDecodeError, ValueError, TypeError):
        return metadata

    if not isinstance(body, dict):
        return metadata

    error_obj = body.get("error")
    if not isinstance(error_obj, dict):
        return metadata

    message = error_obj.get("message")
    if isinstance(message, str) and message.strip():
        metadata["error_message"] = message.strip()

    err_type = error_obj.get("type")
    if isinstance(err_type, str) and err_type.strip():
        metadata["error_type"] = err_type.strip()

    err_code = error_obj.get("code")
    if isinstance(err_code, str) and err_code.strip():
        metadata["error_code"] = err_code.strip()

    return metadata


def _raise_llm_error(
    error_cls: type[AppError],
    message: str,
    *,
    provider_error: dict[str, Any] | None = None,
    cause: Exception | None = None,
) -> NoReturn:
    """Raise an AppError subclass and optionally attach provider metadata.

    This preserves public error responses (still message + code/status) while
    making structured provider metadata available internally for retries,
    failover, and telemetry.
    """
    exc = error_cls(message)
    if provider_error is not None:
        setattr(exc, "provider_error", provider_error)
    if cause is not None:
        raise exc from cause
    raise exc


def _rate_limit_message_from_metadata(metadata: dict[str, Any]) -> str:
    fallback = "LLM rate limit exceeded."
    message = metadata.get("error_message")
    if isinstance(message, str) and message.strip():
        return message
    return fallback


async def call_chat_completion_json(
    *,
    system_prompt: str,
    user_prompt: str,
    error_cls: type[AppError],
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Make a single Chat Completions request (JSON mode) and return the
    raw content string. Raises `error_cls` on any failure — never returns
    a plausible-looking default.
    """
    settings = get_settings()
    provider = settings.ai_provider.lower()

    if provider == "openai":
        if not settings.openai_api_key:
            raise error_cls("OPENAI_API_KEY is not configured.")
        api_key = settings.openai_api_key
        base_url = "https://api.openai.com/v1/chat/completions"
        effective_model = model or settings.openai_model
    elif provider == "groq":
        if not settings.groq_api_key:
            raise error_cls("GROQ_API_KEY is not configured.")
        api_key = settings.groq_api_key
        base_url = "https://api.groq.com/openai/v1/chat/completions"
        effective_model = settings.groq_model
    else:
        raise error_cls(f"Unsupported AI provider: {provider}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": effective_model,
        "temperature": settings.openai_temperature,
        "max_tokens": settings.openai_max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    client = http_client or httpx.AsyncClient()
    should_close = http_client is None

    try:
        response = await client.post(base_url, headers=headers, json=payload, timeout=60.0)
    except httpx.TimeoutException as exc:
        logger.warning(
            "llm_http_timeout provider=%s exception_class=%s model=%s",
            provider,
            exc.__class__.__name__,
            effective_model,
        )
        _raise_llm_error(error_cls, "LLM request timed out.", cause=exc)
    except httpx.HTTPError as exc:
        provider_error = {
            "provider": provider,
            "model": effective_model,
            "status_code": getattr(exc.response, "status_code", None),
            "retry_after": (
                exc.response.headers.get("retry-after")
                if getattr(exc, "response", None) is not None
                else None
            ),
            "error_message": None,
            "error_type": None,
            "error_code": None,
            "transport_exception_class": exc.__class__.__name__,
        }

        status_code = getattr(exc.response, "status_code", None)
        logger.warning(
            "llm_http_error provider=%s model=%s status_code=%s retry_after=%s transport_exception_class=%s",
            provider,
            effective_model,
            status_code,
            provider_error["retry_after"],
            exc.__class__.__name__,
        )
        _raise_llm_error(
            error_cls,
            f"LLM communication error: {exc}",
            provider_error=provider_error,
            cause=exc,
        )
    finally:
        if should_close:
            await client.aclose()

    if response.status_code >= 400:
        provider_error = _extract_provider_error_metadata(
            response,
            provider=provider,
            model=effective_model,
        )
        logger.warning(
            "llm_provider_error provider=%s model=%s status_code=%s retry_after=%s error_type=%s error_code=%s error_message=%r",
            provider_error["provider"],
            provider_error["model"],
            provider_error["status_code"],
            provider_error["retry_after"],
            provider_error["error_type"],
            provider_error["error_code"],
            provider_error["error_message"],
        )

    if response.status_code == 401:
        _raise_llm_error(
            error_cls,
            "LLM API key is invalid.",
            provider_error=provider_error,
        )
    if response.status_code == 429:
        _raise_llm_error(
            error_cls,
            _rate_limit_message_from_metadata(provider_error),
            provider_error=provider_error,
        )
    if response.status_code >= 400:
        _raise_llm_error(
            error_cls,
            f"LLM returned HTTP {response.status_code}.",
            provider_error=provider_error,
        )

    body = response.json()
    try:
        return str(body["choices"][0]["message"]["content"])
    except (KeyError, IndexError) as exc:
        raise error_cls(f"LLM response missing expected fields: {exc}") from exc
