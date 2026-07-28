"""Contract for LLM-backed analysis providers.

Concrete providers (OpenAI, Anthropic, etc.) implement this interface.
The services layer depends only on this port, never on a specific vendor
SDK directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext

if TYPE_CHECKING:
    from app.ai.providers.base import LLMRequestOptions, LLMResponse, ToolSpec, ToolTurnResult


class ILLMProvider(ABC):
    """Port for a Large Language Model provider that powers AI-driven
    analysis features.

    Implementations live in ``app.ai.providers``.  The orchestration
    services in ``app.ai.services`` depend on this interface so that
    swapping providers requires no changes outside the providers package.
    """

    @abstractmethod
    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        options: LLMRequestOptions | None = None,
    ) -> LLMResponse:
        """Transport-only: send caller-supplied prompts to the LLM and
        return a normalized response.

        The provider does NOT build prompts, parse domain-specific JSON,
        or apply any business logic.  Callers own prompt construction,
        response parsing, and error mapping.
        """
        raise NotImplementedError

    @abstractmethod
    async def analyze(self, context: AIContext) -> AIAnalysisResult:
        """(Transitional) Given bounded deterministic analysis context,
        return a complete AI-enriched analysis result.

        This method combines prompt building, transport, and response
        parsing inside the provider.  It will be migrated to use
        :meth:`complete` externally in a future phase.
        """
        raise NotImplementedError

    async def complete_with_tools(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
    ) -> ToolTurnResult:
        """Native tool/function-calling turn — declared here (not
        ``@abstractmethod``) because it is an optional capability, not every
        provider offers native tool-calling. The default raises
        NotImplementedError; ``BaseAnalysisProvider`` in
        ``app.ai.providers.base`` carries the shared default body and
        documents the calling convention, Bedrock is the one provider that
        currently overrides it. Declared on the port (not just the base
        class) so callers that only hold an ``ILLMProvider`` — e.g.
        ``create_llm_provider()``'s return type — can call this and rely on
        catching NotImplementedError, without needing a narrower type.
        """
        raise NotImplementedError
