"""Engineering Review Agent output schema — the T in AgentOutput[T].

Structured readiness assessment produced by the Engineering Review Agent.
Deliberately has no `graph_context_used` / `repositories_consulted`
fields like Planning/Development/Testing — this agent runs no graph
tools of its own (it synthesizes over the prior three stages' already-
graph-grounded outputs), so those fields would be misleading here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CompletenessFinding(BaseModel):
    """Whether one area of the blueprint is sufficiently specified."""

    area: str
    status: str = ""  # "complete" | "incomplete" | "missing"
    detail: str = ""


class RiskAssessment(BaseModel):
    """Whether a risk raised in an earlier stage is adequately mitigated."""

    description: str
    adequately_mitigated: bool = False
    concern: str = ""


class DependencyAssessment(BaseModel):
    """Whether a dependency raised in an earlier stage is accounted for."""

    description: str
    validated: bool = False
    concern: str = ""


class EngineeringReadinessReport(BaseModel):
    """Structured output from the Engineering Review Agent.

    `readiness_status` is the headline verdict; `blocking_issues` are the
    specific reasons a human should reject rather than approve.
    """

    goal: str
    executive_summary: str

    readiness_status: str = ""  # "ready" | "needs_revision" | "not_ready"
    completeness_findings: list[CompletenessFinding] = Field(default_factory=list)
    repository_review: list[str] = Field(default_factory=list)
    component_review: list[str] = Field(default_factory=list)
    risk_assessment: list[RiskAssessment] = Field(default_factory=list)
    dependency_assessment: list[DependencyAssessment] = Field(default_factory=list)
    test_strategy_review: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)

    prompt_version: str = "1.0"
