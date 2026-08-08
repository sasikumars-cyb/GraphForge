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
    for whether the blueprint actually accounts for it.

    `dependency_type`/`confidence`/`evidence` are graph-derived, not
    LLM-assessed: they're backfilled in `_parse_llm_response` by looking the
    named repository up in Context Discovery's own canonical `repositories`
    list (the same `RepositoryCandidate.relationship`/`.confidence`/
    `.reason` fields `RepositorySelector.tsx` already renders), never
    invented by the model. `concern` is the one field the LLM actually
    reasons about — whether the blueprint accounts for the dependency."""

    repository: str
    depends_on: list[str] = Field(default_factory=list)
    concern: str = ""
    # "CALLS_SERVICE" | "SHARES_TOPIC" | "DEPENDS_ON_REPOSITORY" | "" (unknown
    # — e.g. the LLM named a repository Context Discovery never suggested).
    dependency_type: str = ""
    # "structural" | "heuristic" | "" — mirrors the edge's own confidence
    # (see app.indexer.graph.cross_repo_linker's rule set).
    confidence: str = ""
    evidence: list[str] = Field(default_factory=list)

    @field_validator("concern", "dependency_type", "confidence", mode="before")
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
    # produced them. Includes informational/non-blocking entries too, kept
    # for visibility; see `blocking_verification_warnings` for the subset
    # that actually drives the "ready" override below.
    prior_verification_warnings: list[str] = Field(default_factory=list)

    # The subset of prior_verification_warnings classified `blocking` (see
    # app.agents.verification.VerificationFinding) — this, not the mere
    # presence of prior_verification_warnings, is what forces
    # readiness_status to "needs_revision" when the LLM said "ready".
    # Empty whenever every prior finding was informational/non-blocking or
    # there were no prior findings at all.
    blocking_verification_warnings: list[str] = Field(default_factory=list)

    prompt_version: str = "1.0"
