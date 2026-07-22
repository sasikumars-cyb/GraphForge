"""Response schemas for the AI analysis API.

Field names match ``PullRequestAIAnalysis`` columns so that
``ConfigDict(from_attributes=True)`` can hydrate directly from the ORM model.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConfidenceScoreResponse(BaseModel):
    score: float
    reasoning: str = ""


class BreakingChangeResponse(BaseModel):
    component: str
    description: str
    severity: str
    confidence: ConfidenceScoreResponse


class MigrationAdviceResponse(BaseModel):
    component: str
    advice: str
    priority: str


class SuggestedReviewerResponse(BaseModel):
    reviewer: str
    reason: str
    confidence: ConfidenceScoreResponse


class RegressionTestResponse(BaseModel):
    component: str
    test_description: str
    priority: str
    confidence: ConfidenceScoreResponse


class AIAnalysisResponse(BaseModel):
    """Response returned by GET /pull-requests/{id}/ai-analysis."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pull_request_id: uuid.UUID
    executive_summary: str
    breaking_changes: list[BreakingChangeResponse]
    migration_advice: list[MigrationAdviceResponse]
    suggested_reviewers: list[SuggestedReviewerResponse]
    regression_tests: list[RegressionTestResponse]
    confidence_score: float
    confidence_reasoning: str
    prompt_version: str
    analyzed_at: datetime


class AIAnalysisResultResponse(BaseModel):
    """Response returned by POST /pull-requests/{id}/ai-analysis.

    Mirrors ``AIAnalysisResult`` from the schemas package.
    """

    executive_summary: str = ""
    breaking_changes: list[BreakingChangeResponse] = Field(default_factory=list)
    migration_advice: list[MigrationAdviceResponse] = Field(default_factory=list)
    suggested_reviewers: list[SuggestedReviewerResponse] = Field(default_factory=list)
    regression_tests: list[RegressionTestResponse] = Field(default_factory=list)
    confidence: ConfidenceScoreResponse
    prompt_version: str = ""
