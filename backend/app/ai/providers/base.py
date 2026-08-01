"""Shared provider utilities for AI analysis providers.

This module keeps provider-independent behavior in one place so adding new
providers requires only request/response mapping code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.agents.prompt_utils import strip_markdown_fence
from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.errors import AIProviderResponseError
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext
from app.ai.services.prompt_builder import PromptBuilder

# ------------------------------------------------------------------
# Transport-level data types
# ------------------------------------------------------------------


class ResponseFormat(Enum):
    """Output format the caller expects from the LLM."""

    JSON = "json"
    TEXT = "text"


@dataclass(frozen=True)
class LLMRequestOptions:
    """Caller-controlled transport options for a completion request.

    Providers map these to vendor-specific parameters.  Fields that a
    provider does not support are silently ignored, keeping the API
    symmetric across backends.

    Extensible — future transport capabilities (JSON-schema validation,
    tool/function calling, streaming, provider-specific flags) are added
    as new fields with backwards-compatible defaults.
    """

    response_format: ResponseFormat = ResponseFormat.JSON


@dataclass(frozen=True)
class LLMResponse:
    """Normalized completion payload across providers."""

    text: str
    model_name: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ToolSpec:
    """One tool definition offered to the model for a tool-calling turn.

    `input_schema` is a JSON Schema object — passed through to the provider
    largely as-is, since Converse/OpenAI/Gemini tool-calling APIs all accept
    JSON Schema for parameters.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolUseRequest:
    """One tool call the model asked for in a turn."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolTurnResult:
    """One turn of a tool-calling conversation.

    `content_blocks` is the provider-native shape for the assistant's own
    message — callers append this verbatim to the conversation history so
    multi-turn state (including tool_use blocks) round-trips correctly on
    the next call; `tool_uses` is the same information pre-parsed for
    convenience. `text` is the concatenation of any plain-text blocks (the
    model's final answer, once it stops requesting tools).
    """

    content_blocks: list[dict[str, Any]]
    tool_uses: list[ToolUseRequest]
    text: str
    stop_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class BaseAnalysisProvider(ILLMProvider):
    """Provider-agnostic analyze pipeline.

    Subclasses implement provider-specific transport in
    ``_send_completion``.  The public ``complete()`` method is the
    transport-only entry point for callers that own their own prompts and
    response parsing.  The transitional ``analyze()`` method retains the
    existing domain-coupled behavior until all callers are migrated.
    """

    def __init__(self) -> None:
        self._prompt_builder = PromptBuilder()

    # ------------------------------------------------------------------
    # Transport-only public API (new — used by migrated agents)
    # ------------------------------------------------------------------

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        options: LLMRequestOptions | None = None,
    ) -> LLMResponse:
        """Send caller-supplied prompts and return a normalized response.

        Does not build prompts, parse domain-specific JSON, or apply
        business logic.  Raises provider-level exceptions on transport
        errors (timeout, auth, rate-limit, malformed response).
        """
        resolved = options if options is not None else LLMRequestOptions()
        return await self._send_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            options=resolved,
        )

    async def _send_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        options: LLMRequestOptions,
    ) -> LLMResponse:
        """Provider-specific transport.  Subclasses MUST override."""
        raise NotImplementedError

    async def complete_with_tools(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
    ) -> ToolTurnResult:
        """Native tool/function-calling turn, for callers that need the
        model itself to drive a multi-step tool-use loop (as opposed to
        `complete()`, a single fixed prompt -> text call).

        `messages` is provider-native chat history — the caller owns
        building and extending it turn-to-turn (appending
        `ToolTurnResult.content_blocks` as the assistant turn, then a
        tool-result message once it has executed whatever the model asked
        for). This mirrors Converse API's own message shape since that's
        the first (and, for now, only) implementation; a second provider
        adding this would need to translate to/from its own native shape.

        Not every provider implements this — raises NotImplementedError by
        default. Callers must catch that and degrade gracefully (see
        PlanningAgent's Confluence context gathering, which skips that
        enrichment entirely rather than failing the run when the active
        provider doesn't support it).
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Transitional domain-coupled API (existing — used by AI Analysis)
    # ------------------------------------------------------------------

    async def analyze(self, context: AIContext) -> AIAnalysisResult:
        prompt = self._build_prompt(context)
        llm_response = await self._request_completion(prompt)
        return self._parse_response(llm_response.text)

    def _build_prompt(self, context: AIContext) -> str:
        variables = context.to_prompt_variables()
        return self._prompt_builder.render("impact_analysis", variables)

    def _parse_response(self, raw: str) -> AIAnalysisResult:
        try:
            data = json.loads(strip_markdown_fence(raw))
        except json.JSONDecodeError as exc:
            raise AIProviderResponseError("AI provider returned invalid JSON.") from exc

        try:
            result = AIAnalysisResult.model_validate(data)
        except Exception as exc:
            raise AIProviderResponseError(
                "AI provider response does not match expected schema."
            ) from exc

        return result.model_copy(
            update={"prompt_version": self._prompt_builder.extract_version("impact_analysis")}
        )

    @staticmethod
    def build_messages(system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
        """Build a role-tagged chat message list.

        Kept generic so providers can map this list to their native request shape.
        """
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def messages_to_text(messages: list[dict[str, str]]) -> str:
        """Flatten role-tagged messages into provider-neutral text.

        Roles are preserved explicitly to retain intent when converting to
        providers that do not expose OpenAI-style chat message arrays.
        """
        chunks: list[str] = []
        for message in messages:
            role = str(message.get("role", "user")).strip().upper()
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            chunks.append(f"[{role}]\n{content}")
        return "\n\n".join(chunks)

    async def _request_completion(self, user_prompt: str) -> LLMResponse:
        raise NotImplementedError


@dataclass(frozen=True)
class ProviderErrorMetadata:
    provider: str
    model: str
    status_code: int | None
    retry_after: str | None
    error_message: str | None
    error_type: str | None
    error_code: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "status_code": self.status_code,
            "retry_after": self.retry_after,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "error_code": self.error_code,
        }
