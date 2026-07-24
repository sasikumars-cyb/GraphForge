"""OpenAI-compatible implementation of ILLMProvider.

Supports OpenAI and vendors that implement the same Chat Completions shape.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.ai.providers.base import BaseAnalysisProvider, LLMRequestOptions, LLMResponse, ResponseFormat
from app.ai.providers.errors import (
    AIProviderAuthError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
)
from app.ai.providers.http_utils import raise_for_error_response

logger = logging.getLogger(__name__)

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

_SYSTEM_PROMPT = (
    "You are a senior software architect performing AI-enriched impact analysis. "
    "Respond ONLY with valid JSON matching the AIAnalysisResult schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)


class OpenAIProvider(BaseAnalysisProvider):
    """Chat Completions provider for OpenAI-compatible APIs."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        base_url: str = _OPENAI_CHAT_URL,
        provider_name: str = "openai",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__()
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._base_url = base_url
        self._provider_name = provider_name
        self._http_client = http_client

    async def _send_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        options: LLMRequestOptions,
    ) -> LLMResponse:
        """Transport-only: send caller-supplied prompts via Chat Completions."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "messages": self.build_messages(system_prompt, user_prompt),
        }
        if options.response_format == ResponseFormat.JSON:
            payload["response_format"] = {"type": "json_object"}

        client = self._http_client or httpx.AsyncClient()
        should_close = self._http_client is None

        try:
            response = await client.post(
                self._base_url,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            logger.error("AI provider request timed out: %s", exc)
            raise AIProviderTimeoutError("AI provider request timed out.") from exc
        except httpx.HTTPError as exc:
            logger.error("AI provider HTTP error: %s", exc)
            exc_response = getattr(exc, "response", None)
            retry_after = (
                exc_response.headers.get("retry-after")
                if isinstance(exc_response, httpx.Response)
                else None
            )
            meta = {
                "provider": self._provider_name,
                "model": self._model,
                "status_code": (
                    exc_response.status_code if isinstance(exc_response, httpx.Response) else None
                ),
                "retry_after": retry_after,
                "error_message": None,
                "error_type": None,
                "error_code": None,
            }
            error = AIProviderError("AI provider communication error.")
            error.provider_error = meta
            raise error from exc
        finally:
            if should_close:
                await client.aclose()

        raise_for_error_response(
            response,
            provider=self._provider_name,
            model=self._model,
        )
        return self._extract_response(response)

    async def _request_completion(self, user_prompt: str) -> LLMResponse:
        """Transitional: delegates to _send_completion with the built-in
        AI-analysis system prompt.  Used only by analyze()."""
        return await self._send_completion(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            options=LLMRequestOptions(),
        )

    def _extract_response(self, response: httpx.Response) -> LLMResponse:
        """Extract completion text + metadata from Chat Completions JSON."""
        try:
            body = response.json()
            choice = body["choices"][0]
            text = str(choice["message"]["content"])
            usage = body.get("usage", {}) if isinstance(body, dict) else {}
            if not isinstance(usage, dict):
                usage = {}
            return LLMResponse(
                text=text,
                model_name=str(body.get("model")) if body.get("model") else self._model,
                finish_reason=(
                    str(choice.get("finish_reason")) if choice.get("finish_reason") else None
                ),
                prompt_tokens=(
                    int(usage["prompt_tokens"]) if usage.get("prompt_tokens") is not None else None
                ),
                completion_tokens=(
                    int(usage["completion_tokens"])
                    if usage.get("completion_tokens") is not None
                    else None
                ),
                total_tokens=(
                    int(usage["total_tokens"]) if usage.get("total_tokens") is not None else None
                ),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderResponseError(
                "AI provider returned an unexpected response structure."
            ) from exc


__all__ = [
    "AIProviderAuthError",
    "AIProviderError",
    "AIProviderRateLimitError",
    "AIProviderResponseError",
    "AIProviderTimeoutError",
    "OpenAIProvider",
]
