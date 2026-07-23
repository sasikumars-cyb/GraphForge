"""Planning Agent output schema — the T in AgentOutput[T].

Kept separate from the AgentOutput envelope (defined in _contract.py) so
the planning-specific fields don't bleed into the generic contract.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImplementationStep(BaseModel):
    """One ordered step in the implementation plan."""

    order: int = Field(ge=1)
    description: str
    # Which component/service/topic this step touches — empty if it's a
    # cross-cutting concern (e.g. "run regression tests for all services")
    affected_component: str = ""
    risk_note: str = ""


class PlanningResult(BaseModel):
    """Structured output from the Planning Agent.

    `graph_context_used` is True when real graph data informed the plan —
    this is the code-level flag the Evidence list backs up for the
    "GraphForge is NOT just a chatbot" demo claim.
    """

    task_description: str
    executive_summary: str
    implementation_steps: list[ImplementationStep] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)
    kafka_topics_involved: list[str] = Field(default_factory=list)
    risk_considerations: list[str] = Field(default_factory=list)
    graph_context_used: bool = False
    repositories_consulted: list[str] = Field(default_factory=list)
    prompt_version: str = "1.0"
