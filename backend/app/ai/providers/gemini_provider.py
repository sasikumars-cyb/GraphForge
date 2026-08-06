"""Google Gemini implementation of ILLMProvider."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from app.ai.providers.base import BaseAnalysisProvider, LLMRequestOptions, LLMResponse
from app.ai.providers.errors import AIProviderError, AIProviderResponseError, AIProviderTimeoutError
from app.ai.providers.http_utils import raise_for_error_response

logger = logging.getLogger(__name__)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_SYSTEM_PROMPT = (
    "You are a senior software architect performing AI-enriched impact analysis. "
    "Respond ONLY with valid JSON matching the AIAnalysisResult schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)


class GeminiProvider(BaseAnalysisProvider):
    """Provider adapter for Gemini's generateContent REST API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-3.6-flash",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        base_url: str = _GEMINI_BASE_URL,
        provider_name: str = "gemini",
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
        """Transport-only: send caller-supplied prompts via Gemini generateContent."""
        # options.response_format is accepted for API symmetry but not
        # mapped — Gemini’s generateContent API has no equivalent param.
        messages = self.build_messages(system_prompt, user_prompt)
        text_prompt = self.messages_to_text(messages)
        payload: dict[str, Any] = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": text_prompt,
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": self._temperature,
                "maxOutputTokens": self._max_tokens,
            },
        }

        endpoint = (
            f"{self._base_url}/{self._model}:generateContent?"
            f"{urlencode({'key': self._api_key})}"
        )

        client = self._http_client or httpx.AsyncClient()
        should_close = self._http_client is None

        try:
            response = await client.post(
                endpoint,
                headers={"Content-Type": "application/json"},
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
        """Extract completion text + metadata from Gemini JSON."""
        try:
            body = response.json()
            candidates = body["candidates"]
            first = candidates[0]
            content = first["content"]
            parts = content["parts"]

            texts: list[str] = []
            for part in parts:
                text = part.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
            if not texts:
                raise KeyError("No text in candidate parts")

            usage = body.get("usageMetadata", {}) if isinstance(body, dict) else {}
            if not isinstance(usage, dict):
                usage = {}

            prompt_tokens = usage.get("promptTokenCount")
            completion_tokens = usage.get("candidatesTokenCount")
            total_tokens = usage.get("totalTokenCount")
            # Gemini's "thinking" models (this one included) bill a third
            # token category - `thoughtsTokenCount` - that's already
            # folded into `totalTokenCount` but never appears in
            # `candidatesTokenCount`. Left uncaptured, `total_tokens`
            # silently exceeds `prompt_tokens + completion_tokens` by
            # exactly that amount - confirmed against real production
            # `llm_invocations` rows (KAN-metrics follow-up) where a
            # context_discovery synthesis call showed prompt=1328/
            # completion=1164 (sum 2492) against a stored total of 5077.
            # Folded into `completion_tokens` (thinking output is
            # model-generated output, the same category cost-wise) so
            # every consumer of these three fields - the Metrics per-
            # stage breakdown, cost_by_stage, cost_by_day - can rely on
            # the same total = prompt + completion invariant every other
            # provider already upholds, rather than a Gemini-only gap.
            thoughts_tokens = usage.get("thoughtsTokenCount")
            if thoughts_tokens is not None and completion_tokens is not None:
                completion_tokens = int(completion_tokens) + int(thoughts_tokens)

            return LLMResponse(
                text="\n".join(texts),
                model_name=(
                    str(body.get("modelVersion")) if body.get("modelVersion") else self._model
                ),
                finish_reason=(
                    str(first.get("finishReason"))
                    if first.get("finishReason") is not None
                    else None
                ),
                prompt_tokens=int(prompt_tokens) if prompt_tokens is not None else None,
                completion_tokens=(
                    int(completion_tokens) if completion_tokens is not None else None
                ),
                total_tokens=int(total_tokens) if total_tokens is not None else None,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderResponseError(
                "AI provider returned an unexpected response structure."
            ) from exc
