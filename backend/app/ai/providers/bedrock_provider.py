"""Amazon Bedrock implementation of ILLMProvider.

Uses the AWS SDK (boto3) bedrock-runtime client with the Converse API,
which provides a unified interface across all Bedrock-hosted models
(Anthropic Claude, Amazon Nova, Meta Llama, etc.).

Credentials are resolved through the standard AWS credential chain:
environment variables, ~/.aws/credentials, IAM roles, EC2/ECS instance
profiles. GraphForge never stores or handles AWS secret keys directly.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.ai.providers.base import (
    BaseAnalysisProvider,
    LLMRequestOptions,
    LLMResponse,
    ToolSpec,
    ToolTurnResult,
    ToolUseRequest,
)
from app.ai.providers.errors import (
    AIProviderAuthError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
)

try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import (
        ClientError,
        EndpointConnectionError,
        ReadTimeoutError,
    )
except ImportError:  # pragma: no cover - boto3 is a required dependency
    boto3 = None  # type: ignore[assignment]
    BotoConfig = None  # type: ignore[assignment, misc]
    ClientError = EndpointConnectionError = ReadTimeoutError = Exception  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a senior software architect performing AI-enriched impact analysis. "
    "Respond ONLY with valid JSON matching the AIAnalysisResult schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)

# Finish reason normalization: Bedrock Converse API uses these stop reasons.
_FINISH_REASON_MAP: dict[str, str] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "content_filtered": "content_filter",
    "tool_use": "tool_calls",
}


class BedrockProvider(BaseAnalysisProvider):
    """Provider adapter for Amazon Bedrock's Converse API.

    The Converse API is model-agnostic: the same request/response shape works
    for Claude, Nova, Llama, and any future model hosted on Bedrock. This
    means the provider needs no model-specific branching.
    """

    def __init__(
        self,
        *,
        model: str = "us.anthropic.claude-sonnet-4-20250514",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        region: str = "us-east-1",
        provider_name: str = "bedrock",
        # Allow injection for testing.
        bedrock_client: Any | None = None,
    ) -> None:
        super().__init__()
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._region = region
        self._provider_name = provider_name
        self._client = bedrock_client

    def _get_client(self) -> Any:
        """Lazily initialize the Bedrock runtime client.

        Deferred so client construction (and its network/credential setup)
        only happens when Bedrock is actually used, and so tests can inject
        a mock client.
        """
        if self._client is not None:
            return self._client

        if boto3 is None:
            raise AIProviderError(
                "boto3 is required for the Bedrock provider. "
                "Install it with: pip install boto3"
            )

        boto_config = BotoConfig(
            region_name=self._region,
            read_timeout=int(self._timeout),
            connect_timeout=15,
            retries={"max_attempts": 0},  # We handle retries at the fallback layer.
        )
        self._client = boto3.client(
            "bedrock-runtime",
            config=boto_config,
            region_name=self._region,
        )
        return self._client

    async def _send_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        options: LLMRequestOptions,
    ) -> LLMResponse:
        """Send a completion request via Bedrock's Converse API.

        The Converse API provides a unified interface that works across all
        Bedrock models, so no model-specific payload construction is needed.
        """
        import asyncio

        client = self._get_client()

        # Build the Converse API request.
        converse_params: dict[str, Any] = {
            "modelId": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": user_prompt}],
                }
            ],
            "system": [{"text": system_prompt}],
            "inferenceConfig": {
                "temperature": self._temperature,
                "maxTokens": self._max_tokens,
            },
        }

        try:
            # boto3 is synchronous — run in a thread pool to avoid blocking
            # the async event loop.
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.converse(**converse_params),
            )
        except Exception as exc:
            self._handle_client_error(exc)
            # _handle_client_error always raises; this is unreachable but
            # satisfies the type checker.
            raise  # pragma: no cover

        return self._extract_response(response)

    async def complete_with_tools(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
    ) -> ToolTurnResult:
        """Native tool-calling turn via Converse API's `toolConfig` — Claude
        (and other Converse-supported models) can request one or more tool
        calls per turn; the caller executes them and continues the
        conversation by appending a `toolResult` message (see base.py's
        `complete_with_tools` docstring for the full turn-taking contract).
        """
        import asyncio

        client = self._get_client()

        converse_params: dict[str, Any] = {
            "modelId": self._model,
            "messages": messages,
            "system": [{"text": system_prompt}],
            "inferenceConfig": {
                "temperature": self._temperature,
                "maxTokens": self._max_tokens,
            },
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": {"json": t.input_schema},
                        }
                    }
                    for t in tools
                ]
            },
        }

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.converse(**converse_params),
            )
        except Exception as exc:
            self._handle_client_error(exc)
            raise  # pragma: no cover

        return self._extract_tool_turn(response)

    def _extract_tool_turn(self, response: dict[str, Any]) -> ToolTurnResult:
        try:
            output = response["output"]["message"]
            content_blocks = output.get("content", [])

            texts: list[str] = []
            tool_uses: list[ToolUseRequest] = []
            for block in content_blocks:
                if isinstance(block.get("text"), str):
                    texts.append(block["text"])
                elif "toolUse" in block:
                    tu = block["toolUse"]
                    tool_uses.append(
                        ToolUseRequest(
                            id=tu["toolUseId"], name=tu["name"], input=tu.get("input", {})
                        )
                    )

            usage = response.get("usage", {})
            prompt_tokens = usage.get("inputTokens")
            completion_tokens = usage.get("outputTokens")

            return ToolTurnResult(
                content_blocks=content_blocks,
                tool_uses=tool_uses,
                text="\n".join(texts),
                stop_reason=response.get("stopReason"),
                prompt_tokens=int(prompt_tokens) if prompt_tokens is not None else None,
                completion_tokens=int(completion_tokens) if completion_tokens is not None else None,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderResponseError(
                "AI provider returned an unexpected response structure."
            ) from exc

    async def _request_completion(self, user_prompt: str) -> LLMResponse:
        """Transitional: delegates to _send_completion with the built-in
        AI-analysis system prompt. Used only by analyze()."""
        return await self._send_completion(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            options=LLMRequestOptions(),
        )

    def _extract_response(self, response: dict[str, Any]) -> LLMResponse:
        """Extract completion text and metadata from Bedrock Converse response."""
        try:
            # The Converse API returns output.message.content as a list of
            # content blocks. Text blocks have a "text" key.
            output = response["output"]["message"]
            content_blocks = output.get("content", [])

            texts: list[str] = []
            for block in content_blocks:
                text = block.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)

            if not texts:
                raise KeyError("No text content in response")

            # Token usage from the Converse API.
            usage = response.get("usage", {})
            prompt_tokens = usage.get("inputTokens")
            completion_tokens = usage.get("outputTokens")
            total_tokens = usage.get("totalTokens")
            if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
                total_tokens = prompt_tokens + completion_tokens

            # Normalize the stop reason.
            raw_stop_reason = response.get("stopReason", "")
            finish_reason = _FINISH_REASON_MAP.get(raw_stop_reason, raw_stop_reason)

            return LLMResponse(
                text="\n".join(texts),
                model_name=self._model,
                finish_reason=finish_reason if finish_reason else None,
                prompt_tokens=int(prompt_tokens) if prompt_tokens is not None else None,
                completion_tokens=int(completion_tokens) if completion_tokens is not None else None,
                total_tokens=int(total_tokens) if total_tokens is not None else None,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderResponseError(
                "AI provider returned an unexpected response structure."
            ) from exc

    def _handle_client_error(self, exc: Exception) -> None:
        """Normalize boto3/botocore exceptions into the provider error hierarchy.

        This ensures agents receive the same error types regardless of whether
        the response came from OpenAI, Gemini, or Bedrock.
        """
        if boto3 is None:
            raise AIProviderError("boto3/botocore is required for the Bedrock provider.") from exc

        error_meta = {
            "provider": self._provider_name,
            "model": self._model,
            "status_code": None,
            "retry_after": None,
            "error_message": None,
            "error_type": None,
            "error_code": None,
        }

        if isinstance(exc, ReadTimeoutError):
            raise AIProviderTimeoutError("AI provider request timed out.") from exc

        if isinstance(exc, EndpointConnectionError):
            error = AIProviderError("AI provider communication error: unable to reach endpoint.")
            error.provider_error = error_meta
            raise error from exc

        if isinstance(exc, ClientError):
            error_code = exc.response.get("Error", {}).get("Code", "")
            error_message = exc.response.get("Error", {}).get("Message", str(exc))
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")

            error_meta["status_code"] = status_code
            error_meta["error_message"] = error_message
            error_meta["error_code"] = error_code

            # Map Bedrock-specific error codes to the normalized hierarchy.
            if error_code in (
                "UnrecognizedClientException",
                "InvalidSignatureException",
                "ExpiredTokenException",
                "AccessDeniedException",
            ):
                ai_error = AIProviderAuthError(
                    error_message or "Authentication or authorization failed."
                )
                ai_error.provider_error = error_meta
                raise ai_error from exc

            if error_code in ("ThrottlingException", "TooManyRequestsException"):
                ai_error = AIProviderRateLimitError(
                    error_message or "AI provider rate limit exceeded."
                )
                ai_error.provider_error = error_meta
                raise ai_error from exc

            if error_code in ("ModelNotReadyException", "ModelTimeoutException"):
                timeout_error = AIProviderTimeoutError(
                    error_message or "Model not ready or timed out."
                )
                timeout_error.provider_error = error_meta
                raise timeout_error from exc

            if error_code in (
                "ResourceNotFoundException",
                "ModelNotFoundException",
                "ValidationException",
            ):
                response_error = AIProviderResponseError(
                    error_message or f"Model or resource unavailable: {error_code}"
                )
                response_error.provider_error = error_meta
                raise response_error from exc

            if error_code == "ServiceUnavailableException":
                ai_error_generic = AIProviderError(
                    error_message or "Bedrock service unavailable."
                )
                ai_error_generic.status_code = 503
                ai_error_generic.provider_error = error_meta
                raise ai_error_generic from exc

            # Default: generic provider error.
            generic = AIProviderError(error_message or f"Bedrock error: {error_code}")
            generic.provider_error = error_meta
            raise generic from exc

        # Unknown exception type — wrap generically.
        logger.error("Unexpected Bedrock error: %s", exc)
        generic = AIProviderError(f"AI provider communication error: {exc}")
        generic.provider_error = error_meta
        raise generic from exc
