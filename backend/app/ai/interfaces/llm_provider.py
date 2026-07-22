"""Contract for LLM-backed analysis providers.

Concrete providers (OpenAI, Anthropic, etc.) implement this interface.
The services layer depends only on this port, never on a specific vendor
SDK directly.
"""

from abc import ABC, abstractmethod

from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext


class ILLMProvider(ABC):
    """Port for a Large Language Model provider that powers AI-driven
    analysis features.

    Implementations live in ``app.ai.providers``.  The orchestration
    services in ``app.ai.services`` depend on this interface so that
    swapping providers requires no changes outside the providers package.

    A single call to :meth:`analyze` returns the complete AI-enriched
    analysis for a pull request — breaking changes, reviewers, regression
    tests, and migration advice — in one structured response.
    """

    @abstractmethod
    async def analyze(self, context: AIContext) -> AIAnalysisResult:
        """Given bounded deterministic analysis context, return a complete
        AI-enriched analysis result."""
        raise NotImplementedError
