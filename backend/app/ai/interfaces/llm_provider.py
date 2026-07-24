"""Contract for LLM-backed analysis providers.

Concrete providers (OpenAI, Anthropic, etc.) implement this interface.
The services layer depends only on this port, never on a specific vendor
SDK directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext

if TYPE_CHECKING:
    from app.ai.providers.base import LLMRequestOptions, LLMResponse


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
        options: "LLMRequestOptions | None" = None,
    ) -> "LLMResponse":
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
