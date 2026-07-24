"""Shared provider utilities for AI analysis providers.

This module keeps provider-independent behavior in one place so adding new
providers requires only request/response mapping code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.errors import AIProviderResponseError
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext
from app.ai.services.prompt_builder import PromptBuilder


@dataclass(frozen=True)
class LLMResponse:
    """Normalized completion payload across providers."""

    text: str
    model_name: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class BaseAnalysisProvider(ILLMProvider):
    """Provider-agnostic analyze pipeline.

    Subclasses only implement provider-specific request/response mapping
    in ``_request_completion``.
    """

    def __init__(self) -> None:
        self._prompt_builder = PromptBuilder()

    async def analyze(self, context: AIContext) -> AIAnalysisResult:
        prompt = self._build_prompt(context)
        llm_response = await self._request_completion(prompt)
        return self._parse_response(llm_response.text)

    def _build_prompt(self, context: AIContext) -> str:
        variables = context.to_prompt_variables()
        return self._prompt_builder.render("impact_analysis", variables)

    def _parse_response(self, raw: str) -> AIAnalysisResult:
        try:
            data = json.loads(raw)
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
