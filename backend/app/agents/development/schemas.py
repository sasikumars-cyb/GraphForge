"""Development Agent output schema — the T in AgentOutput[T].

Structured implementation blueprint produced by the Development Agent.
Machine-readable, card-friendly for the frontend. No large text blobs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    blueprint: dict | None = Field(default=None)
    prompt_version: str = "1.0"
