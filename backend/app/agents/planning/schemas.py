"""Planning Agent output schema — the T in AgentOutput[T].

Kept separate from the AgentOutput envelope (defined in _contract.py) so
the planning-specific fields don't bleed into the generic contract.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ImplementationStep(BaseModel):
    """One ordered step in the implementation plan."""

    order: int = Field(ge=1)
    description: str
    # Which component/service/topic this step touches — empty if it's a
    # cross-cutting concern (e.g. "run regression tests for all services")
    affected_component: str = ""
    risk_note: str = ""


# ---------------------------------------------------------------------------
# Architect-level blueprint models — produced by the LLM for richer diagrams.
# extra="ignore" so unexpected LLM fields don't fail validation.
# ---------------------------------------------------------------------------


class ArchitectureLayer(BaseModel):
    """One conceptual solution layer (e.g. Landing Zone, Transformation)."""

    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    # source | ingestion | processing | storage | consumption | monitoring
    layer_type: str = "processing"
    order: int = 1


class DataFlowStep(BaseModel):
    """One step in the end-to-end operational data flow."""

    model_config = ConfigDict(extra="ignore")

    name: str
    technology: str = ""
    step_type: str = "process"  # source | process | storage | destination
    order: int = 1


class RepositoryUsage(BaseModel):
    """Analysis of one repository's relevance to the solution.

    Repository intelligence *supports* the architecture rather than defining
    it, so this carries enough detail to justify a reuse decision on its own:
    what the repo does, which capability it satisfies, how much of it we
    expect to reuse, and what the alternative would be.
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    stars: int = 3  # 1-5 relevance rating
    purpose: str = ""  # what this repository does today
    reusable_components: list[str] = Field(default_factory=list)
    reason: str = ""  # which architectural capability it satisfies
    relationship: str = "reuse"  # foundation | reuse | reference
    estimated_reuse_pct: int = 0  # 0-100, how much is reusable as-is
    confidence: str = "medium"  # low | medium | high
    files_affected: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)


class DataEntity(BaseModel):
    """One business domain entity for the ER diagram."""

    model_config = ConfigDict(extra="ignore")

    name: str
    key_attributes: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)


class ImplementationPhase(BaseModel):
    """One engineering phase in the implementation roadmap."""

    model_config = ConfigDict(extra="ignore")

    name: str
    order: int
    deliverables: list[str] = Field(default_factory=list)


class RiskItem(BaseModel):
    """One structured risk with likelihood/impact assessment."""

    model_config = ConfigDict(extra="ignore")

    description: str
    likelihood: str = "medium"  # low | medium | high
    impact: str = "medium"  # low | medium | high | critical
    mitigation: str = ""
    # architecture | operational | security | performance | data_quality |
    # maintainability | dependency (older results may carry v2 categories:
    # schema | data | integration)
    category: str = ""
    evidence: str = ""  # the architectural/graph fact this risk rests on
    confidence: str = "medium"  # low | medium | high


class LLMTrace(BaseModel):
    """The actual prompt sent to the LLM and its raw response — real
    trace-level detail, not the one-line Evidence summary. Truncated (see
    agent.py) to a sane cap before storage; not an unbounded dump."""

    model: str = ""
    prompt: str = ""
    raw_response: str = ""
    latency_ms: int | None = None


class PlanningResult(BaseModel):
    """Structured output from the Planning Agent.

    `graph_context_used` is True when real graph data informed the plan —
    this is the code-level flag the Evidence list backs up for the
    "GraphForge is NOT just a chatbot" demo claim.

    `blueprint` holds the serialized BlueprintArtifact (model_dump()) generated
    deterministically from the structured fields above — stored alongside the
    result so the frontend can render Visual Blueprint diagrams without a
    separate endpoint. Absent when blueprint generation fails (non-blocking).

    The architect-level fields (architecture_layers … risks) are populated when
    the LLM produces them; they default to empty lists so old stored results
    and test fixtures that don't include them continue to work.
    """

    task_description: str
    executive_summary: str
    implementation_steps: list[ImplementationStep] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)
    kafka_topics_involved: list[str] = Field(default_factory=list)
    risk_considerations: list[str] = Field(default_factory=list)
    graph_context_used: bool = False
    repositories_consulted: list[str] = Field(default_factory=list)
    blueprint: dict | None = Field(default=None)
    prompt_version: str = "1.0"

    # Capability analysis (v4) — derived deterministically from the brief
    # before any repository context is assembled, so the architecture is
    # shaped by the business problem rather than by what happens to be
    # indexed. `capabilities` is multi-label: hybrid briefs keep every part.
    # `project_type` now carries the derived architecture pattern key and
    # keeps its old name so stored results stay readable.
    # See planning/classifier.py.
    capabilities: list[str] = Field(default_factory=list)
    project_type: str = "generic"
    project_type_label: str = ""

    # Architect-level blueprint fields (v2 — empty if LLM didn't produce them)
    architecture_layers: list[ArchitectureLayer] = Field(default_factory=list)
    data_flow: list[DataFlowStep] = Field(default_factory=list)
    repository_usage: list[RepositoryUsage] = Field(default_factory=list)
    data_entities: list[DataEntity] = Field(default_factory=list)
    implementation_phases: list[ImplementationPhase] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)

    # The actual prompt sent and the LLM's raw response — everything the Log
    # tab showed before this was just (tool_name, one-line summary) per
    # Evidence entry, never what the model was actually asked or what it
    # actually said back. None for results persisted before this field
    # existed, or if the LLM call failed before a response was captured.
    llm_trace: LLMTrace | None = Field(default=None)
