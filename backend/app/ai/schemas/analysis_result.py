"""Pydantic v2 schemas for AI analysis results.

These are the structured outputs the AI layer returns after enriching
deterministic analysis from ``app.analysis``.  Validation only — no
business logic lives here.
"""

from pydantic import BaseModel, Field


class ConfidenceScore(BaseModel):
    """Numeric confidence in [0.0, 1.0] with an optional explanation."""

    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(default="")


class BreakingChange(BaseModel):
    """A single breaking change identified by the AI layer."""

    component: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    confidence: ConfidenceScore


class MigrationAdvice(BaseModel):
    """AI-generated migration guidance for a breaking change."""

    component: str = Field(min_length=1)
    advice: str = Field(min_length=1)
    priority: str = Field(min_length=1)


class SuggestedReviewer(BaseModel):
    """A reviewer suggested by the AI layer with justification."""

    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    confidence: ConfidenceScore


class RegressionTest(BaseModel):
    """A regression test suggested by the AI layer."""

    component: str = Field(min_length=1)
    test_description: str = Field(min_length=1)
    priority: str = Field(min_length=1)
    confidence: ConfidenceScore


class AIAnalysisResult(BaseModel):
    """Top-level result returned by the AI analysis pipeline.

    Returned by a single :meth:`ILLMProvider.analyze` call — contains all
    AI-enriched insights for a pull request in one structured response.
    """

    executive_summary: str = Field(default="")
    breaking_changes: list[BreakingChange] = Field(default_factory=list)
    migration_advice: list[MigrationAdvice] = Field(default_factory=list)
    suggested_reviewers: list[SuggestedReviewer] = Field(default_factory=list)
    regression_tests: list[RegressionTest] = Field(default_factory=list)
    confidence: ConfidenceScore = Field(default_factory=lambda: ConfidenceScore(score=0.0))
    prompt_version: str = Field(default="")
