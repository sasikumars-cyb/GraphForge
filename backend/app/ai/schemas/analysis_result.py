"""Pydantic v2 schemas for AI analysis results.

These are the structured outputs the AI layer returns after enriching
deterministic analysis from ``app.analysis``.  Validation only — no
business logic lives here.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class Finding(BaseModel):
    """A single review observation outside the change-impact categories
    above (architecture/maintainability/reliability/testing/documentation).

    One shape for every severity rather than four near-identical list
    fields (``critical_findings``, ``high_findings``, ...) — the UI groups
    by ``severity`` for display, the schema doesn't need to duplicate
    itself to support that.
    """

    category: Literal["architecture", "maintainability", "reliability", "testing", "documentation", "other"]
    severity: Literal["critical", "high", "medium", "low"]
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    confidence: ConfidenceScore


class DeploymentStep(BaseModel):
    """One step in a cross-repository deployment sequence."""

    order: int = Field(ge=1)
    repository: str = Field(min_length=1)
    action: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RepositoryToNotify(BaseModel):
    """A repository (team) that should be informed before this change ships.

    ``urgency`` is a closed vocabulary, not free text, so a frontend can
    render it as a consistent badge (e.g. red "blocking" vs. grey
    "advisory") rather than parsing arbitrary phrases like "ASAP" or
    "before deployment" or "high priority".
    """

    repository: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    urgency: Literal["blocking", "advisory"]


class ReleaseCoordinationPlan(BaseModel):
    """AI-synthesized coordination plan for a change spanning repositories.

    Explains and sequences the deterministic engine's already-computed
    cross-repository impact - never discovers dependencies itself. Two
    guarantees are enforced here in code, not just requested in the prompt
    (this provider doesn't use strict JSON-schema-enforced output, so a
    prose instruction alone isn't reliable):

    - A single "deployment order" of one repository is a contradiction in
      terms - it's cleared automatically unless at least two distinct
      repositories are named (see :meth:`_no_order_for_a_single_repository`).
    - Only repositories the deterministic engine actually found may appear
      anywhere in the plan - see :meth:`grounded_in`, which the caller
      applies after the fact using the exact repository names that were in
      context, filtering out anything the model invented.
    """

    deployment_order: list[DeploymentStep] = Field(default_factory=list)
    repositories_to_notify: list[RepositoryToNotify] = Field(default_factory=list)
    rollout_strategy: str = Field(default="")
    backward_compatibility_advice: str = Field(default="")
    communication_summary: str = Field(default="")
    rollout_risks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_order_for_a_single_repository(self) -> "ReleaseCoordinationPlan":
        distinct_repos = {step.repository for step in self.deployment_order}
        if len(distinct_repos) <= 1:
            self.deployment_order = []
        return self

    def grounded_in(
        self, known_repository_names: set[str], current_repository_name: str
    ) -> "ReleaseCoordinationPlan":
        """Strip any deployment step or notify entry referencing a
        repository that isn't actually in ``known_repository_names``, and
        drop any attempt to "notify" the current repository about itself.

        A deterministic backstop against the model inventing or
        hallucinating a repository - independent of whether it followed the
        prompt's instructions. Rebuilding via the normal constructor (not
        ``model_construct``) re-runs validation, including
        :meth:`_no_order_for_a_single_repository` against the filtered
        result.
        """
        filtered_steps = [
            step for step in self.deployment_order if step.repository in known_repository_names
        ]
        filtered_notify = [
            entry
            for entry in self.repositories_to_notify
            if entry.repository in known_repository_names
            and entry.repository != current_repository_name
        ]
        return ReleaseCoordinationPlan(
            deployment_order=filtered_steps,
            repositories_to_notify=filtered_notify,
            rollout_strategy=self.rollout_strategy,
            backward_compatibility_advice=self.backward_compatibility_advice,
            communication_summary=self.communication_summary,
            rollout_risks=self.rollout_risks,
        )


class AIAnalysisResult(BaseModel):
    """Top-level result returned by the AI analysis pipeline.

    Returned by a single :meth:`ILLMProvider.analyze` call — contains all
    AI-enriched insights for a pull request in one structured response.

    Fields below ``prompt_version`` (quality/risk score through
    ``suggested_improvements``) were added to turn this from a
    breaking-change/migration-focused report into a general-purpose PR
    review. All are additive with safe defaults, so existing persisted
    rows (`PullRequestAIAnalysis`) and the non-agent single-shot path
    (`AIAnalysisService`, which shares this exact schema and prompt) keep
    validating unchanged — a review generated before this change simply
    has empty/unset values for the new fields.
    """

    executive_summary: str = Field(default="")
    breaking_changes: list[BreakingChange] = Field(default_factory=list)
    migration_advice: list[MigrationAdvice] = Field(default_factory=list)
    suggested_reviewers: list[SuggestedReviewer] = Field(default_factory=list)
    regression_tests: list[RegressionTest] = Field(default_factory=list)
    release_coordination_plan: ReleaseCoordinationPlan = Field(
        default_factory=ReleaseCoordinationPlan
    )
    confidence: ConfidenceScore = Field(default_factory=lambda: ConfidenceScore(score=0.0))
    prompt_version: str = Field(default="")

    # -- General-purpose review fields (additive) --------------------------
    quality_score: float | None = Field(default=None, ge=0.0, le=100.0)
    risk_score: float | None = Field(default=None, ge=0.0, le=100.0)
    merge_recommendation: (
        Literal["approve", "approve_with_comments", "request_changes", "block"] | None
    ) = Field(default=None)
    findings: list[Finding] = Field(default_factory=list)
    architecture_observations: list[str] = Field(default_factory=list)
    maintainability_observations: list[str] = Field(default_factory=list)
    reliability_observations: list[str] = Field(default_factory=list)
    testing_review: str = Field(default="")
    documentation_review: str = Field(default="")
    positive_findings: list[str] = Field(default_factory=list)
    suggested_improvements: list[str] = Field(default_factory=list)
