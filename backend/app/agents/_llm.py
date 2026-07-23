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

import re
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError


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
        raise error_cls("LLM request timed out.") from exc
    except httpx.HTTPError as exc:
        raise error_cls(f"LLM communication error: {exc}") from exc
    finally:
        if should_close:
            await client.aclose()

    if response.status_code == 401:
        raise error_cls("LLM API key is invalid.")
    if response.status_code == 429:
        raise error_cls("LLM rate limit exceeded.")
    if response.status_code >= 400:
        raise error_cls(f"LLM returned HTTP {response.status_code}.")

    body = response.json()
    try:
        return str(body["choices"][0]["message"]["content"])
    except (KeyError, IndexError) as exc:
        raise error_cls(f"LLM response missing expected fields: {exc}") from exc
