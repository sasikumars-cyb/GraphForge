"""OpenAI-backed implementation of :class:`~app.ai.interfaces.llm_provider.ILLMProvider`.

Uses the OpenAI Chat Completions API with JSON mode to produce a structured
:class:`~app.ai.schemas.analysis_result.AIAnalysisResult`.

All vendor-specific logic is isolated in this module.  The rest of the
application interacts only via the ``ILLMProvider`` interface.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext
from app.ai.services.prompt_builder import PromptBuilder
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

# -- Provider-specific exceptions -------------------------------------------


class AIProviderError(AppError):
    """Base class for AI provider errors.  Never exposes raw vendor details."""

    status_code = 502
    error_code = "ai_provider_error"


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


# -- Provider implementation ------------------------------------------------

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

_SYSTEM_PROMPT = (
    "You are a senior software architect performing AI-enriched impact analysis. "
    "Respond ONLY with valid JSON matching the AIAnalysisResult schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)


class OpenAIProvider(ILLMProvider):
    """Chat Completions provider implementing :class:`ILLMProvider`.

    Renders prompt templates via :class:`PromptBuilder`, sends a single
    request, and parses the JSON response into an :class:`AIAnalysisResult`.

    Despite the name, this also serves as the client for any vendor whose
    API is a compatible superset of OpenAI's Chat Completions format (same
    ``Authorization: Bearer``, ``messages``/``response_format`` request
    shape, and ``choices[0].message.content`` response shape) - e.g. Groq,
    a free-tier alternative with no billing required. Pass that vendor's
    ``base_url`` and API key; nothing else in this class is OpenAI-specific.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        base_url: str = _OPENAI_CHAT_URL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._base_url = base_url
        self._http_client = http_client
        self._prompt_builder = PromptBuilder()

    async def analyze(self, context: AIContext) -> AIAnalysisResult:
        """Send the analysis context to OpenAI and return a parsed result."""
        prompt = self._build_prompt(context)
        raw_response = await self._call_openai(prompt)
        return self._parse_response(raw_response)

    def _build_prompt(self, context: AIContext) -> str:
        """Render the impact_analysis template with context variables."""
        variables = context.to_prompt_variables()
        return self._prompt_builder.render("impact_analysis", variables)

    async def _call_openai(self, user_prompt: str) -> str:
        """Make the HTTP request to ``self._base_url`` and return the content string."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }

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
            raise AIProviderError("AI provider communication error.") from exc
        finally:
            if should_close:
                await client.aclose()

        self._check_status(response)
        return self._extract_content(response)

    def _check_status(self, response: httpx.Response) -> None:
        """Map HTTP status codes to domain exceptions."""
        if response.status_code == 200:
            return
        if response.status_code == 401:
            logger.error("AI provider auth failure (401)")
            raise AIProviderAuthError("Invalid or missing API key.")
        if response.status_code == 429:
            logger.warning("AI provider rate limit hit (429)")
            raise AIProviderRateLimitError("AI provider rate limit exceeded.")
        logger.error("AI provider error %d: %s", response.status_code, response.text[:200])
        raise AIProviderError(f"AI provider returned status {response.status_code}.")

    def _extract_content(self, response: httpx.Response) -> str:
        """Extract the message content from the response JSON (Chat
        Completions shape - shared by OpenAI and OpenAI-compatible vendors)."""
        try:
            body = response.json()
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Malformed AI provider response structure: %s", exc)
            raise AIProviderResponseError(
                "AI provider returned an unexpected response structure."
            ) from exc

    def _parse_response(self, raw: str) -> AIAnalysisResult:
        """Parse the raw JSON string into a validated AIAnalysisResult."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("AI provider response is not valid JSON: %s", exc)
            raise AIProviderResponseError("AI provider returned invalid JSON.") from exc

        try:
            result = AIAnalysisResult.model_validate(data)
        except Exception as exc:
            logger.error("AI provider response failed schema validation: %s", exc)
            raise AIProviderResponseError(
                "AI provider response does not match expected schema."
            ) from exc

        # Attach prompt version from the template
        result = result.model_copy(
            update={"prompt_version": self._prompt_builder.extract_version("impact_analysis")}
        )
        return result
