"""Development Agent output schema — the T in AgentOutput[T].

Structured implementation blueprint produced by the Development Agent.
Machine-readable, card-friendly for the frontend. No large text blobs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents._contract import ComponentWarning


class AffectedRepository(BaseModel):
    """A repository that requires changes."""

    name: str
    owner: str = ""
    reason: str = ""


class AffectedComponent(BaseModel):
    """A service/component that requires modification."""

    name: str
    component_type: str = ""  # Controller, Service, FeignClient, Listener, etc.
    repository: str = ""
    file_path: str = ""
    change_description: str = ""


class Dependency(BaseModel):
    """A dependency relationship relevant to the implementation."""

    source: str
    target: str
    relationship: str = ""  # CALLS, PRODUCES_TO, CONSUMES_FROM, DEPENDS_ON
    risk_note: str = ""


class ReusableImplementation(BaseModel):
    """An existing component/pattern that can be reused."""

    name: str
    repository: str = ""
    reason: str = ""


class ImplementationPhase(BaseModel):
    """One ordered phase in the implementation blueprint."""

    order: int = Field(ge=1)
    title: str
    description: str
    affected_components: list[str] = Field(default_factory=list)
    estimated_complexity: str = ""  # low, medium, high
    depends_on_phases: list[int] = Field(default_factory=list)


class Risk(BaseModel):
    """An architectural risk identified during change planning."""

    description: str
    severity: str = ""  # low, medium, high, critical
    affected_component: str = ""
    mitigation: str = ""


class DevelopmentPlan(BaseModel):
    """Structured output from the Development Agent.

    Every field is designed for card-based rendering in the frontend.
    `graph_context_used` is True when real graph data informed the plan.
    """

    goal: str
    executive_summary: str

    repositories: list[AffectedRepository] = Field(default_factory=list)
    components: list[AffectedComponent] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    reusable_implementations: list[ReusableImplementation] = Field(default_factory=list)
    implementation_phases: list[ImplementationPhase] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)

    graph_context_used: bool = False
    repositories_consulted: list[str] = Field(default_factory=list)
    blueprint: dict[str, Any] | None = Field(default=None)
    prompt_version: str = "1.0"

    # Deterministic, non-LLM warnings: components/files/repositories cited
    # above that do not appear in this run's own tool-returned evidence.
    # See app.agents.verification. Empty when nothing was flagged.
    verification_warnings: list[str] = Field(default_factory=list)

    # Structured counterpart, populated by this agent's OWN independent
    # call to app.agents.component_grounding.check_test_used_as_production
    # against the same graph_components it already reads from Context
    # Discovery — not inherited from Planning's own component_warnings.
    # See PlanningResult.component_warnings for the field's shape/intent.
    component_warnings: list[ComponentWarning] = Field(default_factory=list)
