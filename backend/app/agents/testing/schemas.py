"""Test Planning Agent output schema — the T in AgentOutput[T].

Structured testing strategy produced by the Test Planning Agent.
Machine-readable, card-friendly for the frontend. No large text blobs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents._contract import ComponentWarning


class TestScope(BaseModel):
    """Overall boundaries of what should be tested."""

    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)


class RegressionTest(BaseModel):
    """A regression test that must pass after the change."""

    component: str
    description: str
    priority: str = ""  # critical, high, medium, low
    automated: bool = False


class IntegrationTest(BaseModel):
    """An integration test validating cross-component behavior."""

    source_component: str
    target_component: str
    relationship: str = ""  # CALLS, PRODUCES_TO, CONSUMES_FROM
    description: str
    priority: str = ""


class EdgeCase(BaseModel):
    """An edge case or negative scenario to validate."""

    description: str
    component: str = ""
    severity: str = ""  # critical, high, medium, low
    category: str = ""  # boundary, null_handling, concurrency, timeout, etc.


class EnvironmentRequirement(BaseModel):
    """An environment needed for testing."""

    name: str
    description: str = ""
    services_required: list[str] = Field(default_factory=list)


class AutomationCandidate(BaseModel):
    """A test that should be automated."""

    description: str
    component: str = ""
    test_type: str = ""  # unit, integration, e2e, performance
    reason: str = ""


class ManualValidation(BaseModel):
    """A test that requires manual validation."""

    description: str
    component: str = ""
    reason: str = ""  # why it can't be automated


class ExecutionPhase(BaseModel):
    """An ordered phase in test execution."""

    order: int = Field(ge=1)
    title: str
    description: str
    test_types: list[str] = Field(default_factory=list)
    depends_on_phases: list[int | str] = Field(default_factory=list)


class TestRisk(BaseModel):
    """A risk identified during test planning."""

    description: str
    severity: str = ""  # low, medium, high, critical
    affected_component: str = ""
    mitigation: str = ""


class TestPlan(BaseModel):
    """Structured output from the Test Planning Agent.

    Every field is designed for card-based rendering in the frontend.
    `graph_context_used` is True when real graph data informed the plan.
    """

    goal: str
    executive_summary: str

    test_scope: TestScope = Field(default_factory=TestScope)
    affected_repositories: list[str] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)

    regression_tests: list[RegressionTest] = Field(default_factory=list)
    integration_tests: list[IntegrationTest] = Field(default_factory=list)
    edge_cases: list[EdgeCase] = Field(default_factory=list)

    environment_requirements: list[EnvironmentRequirement] = Field(default_factory=list)
    execution_order: list[ExecutionPhase] = Field(default_factory=list)
    automation_candidates: list[AutomationCandidate] = Field(default_factory=list)
    manual_validations: list[ManualValidation] = Field(default_factory=list)

    risks: list[TestRisk] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)

    graph_context_used: bool = False
    repositories_consulted: list[str] = Field(default_factory=list)
    prompt_version: str = "1.0"

    # Deterministic, non-LLM warnings: components/repositories cited above
    # that do not appear in this run's own tool-returned evidence. See
    # app.agents.verification. The "this is a test PLAN, not executed test
    # results" disclaimer is NOT in this list — it's a true-on-every-run
    # statement of fact, not a claim checked against evidence, so it lives
    # in `execution_status_note` below instead (see that field's docstring
    # for why keeping it here made "ready" unreachable for every workflow).
    verification_warnings: list[str] = Field(default_factory=list)

    # Classified counterpart to verification_warnings above — see
    # PlanningResult.verification_findings for the field's shape/intent.
    verification_findings: list[dict[str, str | bool]] = Field(default_factory=list)

    # Always populated: a test plan is a proposal, never a report of what
    # actually ran. Kept as its own field (informational, never a
    # `verification_warnings`/`verification_findings` entry) precisely so
    # its permanent presence can never affect readiness or confidence —
    # see app.agents.verification's module docstring, point 3.
    execution_status_note: str = (
        "This is a test PLAN produced by an LLM — no test in it has actually "
        "been executed. Regression/integration/edge-case results below are "
        "proposed coverage, not verified pass/fail outcomes."
    )

    # Structured counterpart, populated by this agent's OWN independent
    # call to app.agents.component_grounding.check_test_used_as_production.
    # See PlanningResult.component_warnings for the field's shape/intent.
    component_warnings: list[ComponentWarning] = Field(default_factory=list)
