"""Response schemas for the AI analysis API.

Field names match ``PullRequestAIAnalysis`` columns so that
``ConfigDict(from_attributes=True)`` can hydrate directly from the ORM model.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RunAIAnalysisRequest(BaseModel):
    """Optional request body for POST .../ai-analysis and .../investigate.

    ``model`` lets the UI pick which supported OpenAI model this run uses
    (see `app.ai.providers.factory.SUPPORTED_OPENAI_MODELS`) - validated
    against that closed list, never a free-text vendor string. Omitted or
    ``null`` falls back to the server's configured default model.
    """

    model: str | None = None


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


class DeploymentStepResponse(BaseModel):
    order: int
    repository: str
    action: str
    reason: str


class RepositoryToNotifyResponse(BaseModel):
    repository: str
    reason: str
    urgency: Literal["blocking", "advisory"]


class ReleaseCoordinationPlanResponse(BaseModel):
    deployment_order: list[DeploymentStepResponse]
    repositories_to_notify: list[RepositoryToNotifyResponse]
    rollout_strategy: str
    backward_compatibility_advice: str
    communication_summary: str
    rollout_risks: list[str]


class FindingResponse(BaseModel):
    category: str
    severity: str
    title: str
    description: str
    confidence: ConfidenceScoreResponse


class FileReviewResponse(BaseModel):
    file: str
    complexity: str
    risk: str
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    summary: str = ""


class GeneralReviewFieldsMixin(BaseModel):
    """Shared general-purpose review fields, mixed into every response
    shape that carries a full ``AIAnalysisResult`` (POST .../ai-analysis,
    GET .../ai-analysis, POST .../investigate) so they stay in lockstep
    instead of drifting across three hand-copied field lists."""

    quality_score: float | None = None
    risk_score: float | None = None
    merge_recommendation: (
        Literal["approve", "approve_with_comments", "request_changes", "block"] | None
    ) = None
    findings: list[FindingResponse] = Field(default_factory=list)
    architecture_observations: list[str] = Field(default_factory=list)
    maintainability_observations: list[str] = Field(default_factory=list)
    reliability_observations: list[str] = Field(default_factory=list)
    testing_review: str = ""
    documentation_review: str = ""
    positive_findings: list[str] = Field(default_factory=list)
    suggested_improvements: list[str] = Field(default_factory=list)
    security_score: float | None = None
    testing_score: float | None = None
    documentation_score: float | None = None
    architecture_score: float | None = None
    performance_score: float | None = None
    maintainability_score: float | None = None
    file_reviews: list[FileReviewResponse] = Field(default_factory=list)


class AIAnalysisResponse(GeneralReviewFieldsMixin):
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


class AIAnalysisResultResponse(GeneralReviewFieldsMixin):
    """Response returned by POST /pull-requests/{id}/ai-analysis.

    Mirrors ``AIAnalysisResult`` from the schemas package.
    """

    executive_summary: str = ""
    breaking_changes: list[BreakingChangeResponse] = Field(default_factory=list)
    migration_advice: list[MigrationAdviceResponse] = Field(default_factory=list)
    suggested_reviewers: list[SuggestedReviewerResponse] = Field(default_factory=list)
    regression_tests: list[RegressionTestResponse] = Field(default_factory=list)
    release_coordination_plan: ReleaseCoordinationPlanResponse
    confidence: ConfidenceScoreResponse
    prompt_version: str = ""


class ObservationResponse(BaseModel):
    """What a single tool call returned, for the reasoning log."""

    tool_name: str
    summary: str


class ReasoningStepResponse(BaseModel):
    """One iteration of the Change Investigation Agent's Plan -> Select
    Tool -> Execute -> Observe -> Decide loop. ``tool_selected`` is
    ``None`` for a step where the agent decided evidence wasn't needed -
    a skip is itself a recorded decision, not a gap in the log.
    """

    step_number: int
    goal: str
    plan: str
    tool_selected: str | None
    observation: ObservationResponse | None
    decision: str


class InvestigationResponse(GeneralReviewFieldsMixin):
    """Response returned by POST /pull-requests/{id}/investigate.

    Mirrors ``AIAnalysisResultResponse`` plus the agent's full,
    explainable reasoning log.
    """

    executive_summary: str = ""
    breaking_changes: list[BreakingChangeResponse] = Field(default_factory=list)
    migration_advice: list[MigrationAdviceResponse] = Field(default_factory=list)
    suggested_reviewers: list[SuggestedReviewerResponse] = Field(default_factory=list)
    regression_tests: list[RegressionTestResponse] = Field(default_factory=list)
    release_coordination_plan: ReleaseCoordinationPlanResponse
    confidence: ConfidenceScoreResponse
    prompt_version: str = ""
    reasoning_log: list[ReasoningStepResponse] = Field(default_factory=list)


class PublishReviewResponse(BaseModel):
    """Response returned by POST /pull-requests/{id}/publish-review.

    No persisted counterpart to mirror flatly - GitHub itself is the
    source of truth for the comment after this call.
    """

    comment_id: int
    comment_url: str
