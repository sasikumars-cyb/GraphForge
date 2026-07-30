"""Engineering Review Agent output schema — the T in AgentOutput[T].

Structured readiness assessment produced by the Engineering Review Agent.
Deliberately has no `graph_context_used` / `repositories_consulted`
fields like Planning/Development/Testing — this agent runs no graph
tools of its own (it synthesizes over the prior three stages' already-
graph-grounded outputs), so those fields would be misleading here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _none_to_empty(v: str | None) -> str:
    """Coerce ``null`` from LLM JSON into an empty string."""
    return v if v is not None else ""


class CompletenessFinding(BaseModel):
    """Whether one area of the blueprint is sufficiently specified."""

    area: str
    status: str = ""  # "complete" | "incomplete" | "missing"
    detail: str = ""

    @field_validator("status", "detail", mode="before")
    @classmethod
    def _coerce_none(cls, v: str | None) -> str:
        return _none_to_empty(v)


class RiskAssessment(BaseModel):
    """Whether a risk raised in an earlier stage is adequately mitigated."""

    description: str
    adequately_mitigated: bool = False
    concern: str = ""

    @field_validator("concern", mode="before")
    @classmethod
    def _coerce_none(cls, v: str | None) -> str:
        return _none_to_empty(v)


class DependencyAssessment(BaseModel):
    """Whether a dependency raised in an earlier stage is accounted for."""

    description: str
    validated: bool = False
    concern: str = ""

    @field_validator("concern", mode="before")
    @classmethod
    def _coerce_none(cls, v: str | None) -> str:
        return _none_to_empty(v)


class CrossRepositoryImpact(BaseModel):
    """One repository's cross-repository blast radius, as named in the
    Context Discovery stage's own explicit/suggested repository relationships
    (real graph edges — see `app.indexer.graph.cross_repo_linker`), reviewed
    for whether the blueprint actually accounts for it."""

    repository: str
    depends_on: list[str] = Field(default_factory=list)
    concern: str = ""

    @field_validator("concern", mode="before")
    @classmethod
    def _coerce_none(cls, v: str | None) -> str:
        return _none_to_empty(v)


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
    # Cross-repository impact analysis — populated when Context Discovery
    # found more than one repository in scope (explicit or suggested via a
    # real graph relationship); empty for a single-repository blueprint,
    # same as every other list field here when the review has nothing to
    # say about it.
    cross_repository_impact: list[CrossRepositoryImpact] = Field(default_factory=list)
    test_strategy_review: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)

    # Deterministic verification_warnings carried forward from Planning/
    # Development/Testing (see app.agents.verification) — never generated
    # by this agent's own LLM call, only read from the stages that
    # produced them and used to override an over-optimistic "ready" verdict.
    prior_verification_warnings: list[str] = Field(default_factory=list)

    prompt_version: str = "1.0"
