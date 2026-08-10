"""OpenAI-compatible implementation of ILLMProvider.

Supports OpenAI and vendors that implement the same Chat Completions shape.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.ai.providers.base import (
    BaseAnalysisProvider,
    LLMRequestOptions,
    LLMResponse,
    ResponseFormat,
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

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        """Shared transport for every Chat Completions request this
        provider sends — plain completions (`_send_completion`) and
        tool-calling turns (`complete_with_tools`) differ only in what goes
        into `payload`, not in how it's sent, retried-for-errors, or
        mapped to this codebase's provider-error types. One implementation
        keeps that mapping (timeout/HTTP-error/rate-limit/auth handling)
        from drifting between the two call sites.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
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
        return response

    async def _send_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        options: LLMRequestOptions,
    ) -> LLMResponse:
        """Transport-only: send caller-supplied prompts via Chat Completions."""
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "messages": self.build_messages(system_prompt, user_prompt),
        }
        if options.response_format == ResponseFormat.JSON:
            payload["response_format"] = {"type": "json_object"}

        response = await self._post(payload)
        return self._extract_response(response)

    async def complete_with_tools(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
    ) -> ToolTurnResult:
        """Native tool-calling turn via Chat Completions' `tools`/
        `tool_calls` — the OpenAI-wire-format equivalent of Bedrock's
        Converse `toolConfig` turn.

        `messages`/the returned `content_blocks` use the same
        provider-neutral, Converse-native shape every `complete_with_tools`
        caller builds and round-trips (see base.py's own docstring: Converse
        was "the first (and, for now, only) implementation" — this is the
        second, so it translates that shape to/from OpenAI's own wire format
        at this boundary, rather than asking every caller — and every other
        OpenAI-compatible provider sharing this class (Groq, DeepSeek,
        Cerebras, OpenRouter, Ollama) — to know two message shapes).
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                *_to_openai_messages(messages),
            ],
            "tools": [_tool_spec_to_openai(tool) for tool in tools],
        }
        response = await self._post(payload)
        return self._extract_tool_turn(response)

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

    def _extract_tool_turn(self, response: httpx.Response) -> ToolTurnResult:
        """Extract a tool-calling turn from Chat Completions JSON, mapped
        back into the same Converse-native `content_blocks`/`tool_uses`
        shape `_to_openai_messages` above expects on the *next* turn — the
        round-trip is what lets a caller like `gather_confluence_context`
        append `result.content_blocks` verbatim without knowing which
        provider produced them.
        """
        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            text = message.get("content") or ""
            raw_tool_calls = message.get("tool_calls") or []

            content_blocks: list[dict[str, Any]] = []
            if text:
                content_blocks.append({"text": text})

            tool_uses: list[ToolUseRequest] = []
            for call in raw_tool_calls:
                function = call.get("function", {})
                name = str(function.get("name", ""))
                raw_arguments = function.get("arguments") or "{}"
                try:
                    tool_input = json.loads(raw_arguments)
                except (TypeError, ValueError):
                    # A model that emits malformed JSON arguments must not
                    # crash the whole turn — surface an empty input rather
                    # than raising, the same "don't trust free text/model
                    # output structurally" posture the codebase already
                    # applies elsewhere (see confluence_context.py's own
                    # comment on this).
                    tool_input = {}
                tool_id = str(call.get("id", ""))
                content_blocks.append(
                    {"toolUse": {"toolUseId": tool_id, "name": name, "input": tool_input}}
                )
                tool_uses.append(ToolUseRequest(id=tool_id, name=name, input=tool_input))

            usage = body.get("usage", {}) if isinstance(body, dict) else {}
            if not isinstance(usage, dict):
                usage = {}
            finish_reason = choice.get("finish_reason")
            stop_reason = "tool_calls" if raw_tool_calls else finish_reason

            return ToolTurnResult(
                content_blocks=content_blocks,
                tool_uses=tool_uses,
                text=text,
                stop_reason=str(stop_reason) if stop_reason else None,
                prompt_tokens=(
                    int(usage["prompt_tokens"]) if usage.get("prompt_tokens") is not None else None
                ),
                completion_tokens=(
                    int(usage["completion_tokens"])
                    if usage.get("completion_tokens") is not None
                    else None
                ),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderResponseError(
                "AI provider returned an unexpected response structure."
            ) from exc


def _tool_spec_to_openai(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _stringify_tool_result_content(blocks: list[dict[str, Any]]) -> str:
    """Converse `toolResult.content` is a list of `{"json": ...}` /
    `{"text": ...}` blocks; OpenAI's `tool` role message wants one plain
    string. Concatenates rather than picking one, so a caller that ever
    sends both survives instead of silently losing a block."""
    parts: list[str] = []
    for block in blocks:
        if "json" in block:
            parts.append(json.dumps(block["json"]))
        elif isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Converse-native turn history into OpenAI's wire format.

    Every `complete_with_tools` caller builds its `messages` list against
    the Converse shape (see this module's own `complete_with_tools`
    docstring) — three shapes to recognize on the way in:

    - a plain user turn: ``{"role": "user", "content": [{"text": ...}]}``
    - an assistant turn this provider itself returned last call, appended
      verbatim by the caller: ``{"role": "assistant", "content":
      result.content_blocks}`` (a mix of ``{"text": ...}`` and
      ``{"toolUse": {...}}`` blocks)
    - a tool-result turn: ``{"role": "user", "content": [{"toolResult":
      {...}}, ...]}`` — OpenAI has no single-message equivalent; each
      `toolResult` block becomes its own ``role: "tool"`` message.
    """
    translated: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content") or []
        if not isinstance(content, list):
            translated.append(message)
            continue

        if role == "user" and content and all("toolResult" in block for block in content):
            for block in content:
                tool_result = block["toolResult"]
                translated.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(tool_result.get("toolUseId", "")),
                        "content": _stringify_tool_result_content(tool_result.get("content", [])),
                    }
                )
            continue

        if role == "assistant":
            text_parts = [block["text"] for block in content if isinstance(block.get("text"), str)]
            tool_calls = [
                {
                    "id": block["toolUse"]["toolUseId"],
                    "type": "function",
                    "function": {
                        "name": block["toolUse"]["name"],
                        "arguments": json.dumps(block["toolUse"].get("input", {})),
                    },
                }
                for block in content
                if "toolUse" in block
            ]
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text_parts) or None,
            }
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            translated.append(assistant_message)
            continue

        # Plain user turn (or any other role passed through as-is).
        text = "\n".join(block["text"] for block in content if isinstance(block.get("text"), str))
        translated.append({"role": role or "user", "content": text})

    return translated


__all__ = [
    "AIProviderAuthError",
    "AIProviderError",
    "AIProviderRateLimitError",
    "AIProviderResponseError",
    "AIProviderTimeoutError",
    "OpenAIProvider",
]
