"""Planning Agent output schema — the T in AgentOutput[T].

Kept separate from the AgentOutput envelope (defined in _contract.py) so
the planning-specific fields don't bleed into the generic contract.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents._contract import ComponentWarning


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
    stars: int = 3  # 1-5 — overwritten by the agent from the deterministic
    # repo_score ranking (app.agents.planning.tools.rank_repositories), same
    # ground truth used to pick which repos reach the LLM prompt at all.
    # The LLM's own free-generated value is never trusted for this field.
    purpose: str = ""  # what this repository does today
    reusable_components: list[str] = Field(default_factory=list)
    reason: str = ""  # which architectural capability it satisfies
    relationship: str = "reuse"  # foundation | reuse | reference
    estimated_reuse_pct: int = 0  # 0-100, how much is reusable as-is
    confidence: str = "medium"  # low | medium | high
    files_affected: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    # True only when the agent explicitly confirmed this repository name
    # exists among what the graph traversal returned *and* every one of
    # its files_affected claims checked out against that repository's own
    # evidence (see app.agents.verification) — never the field's own
    # default. Fails closed: a result that somehow reached the frontend
    # without the agent's verification block running (e.g. a hand-built
    # fixture in a test) reads as unverified rather than silently trusted.
    verified: bool = False


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
    agent.py) to a sane cap before storage; not an unbounded dump.

    `provider` is which provider actually served the request — may differ
    from the configured default when a rate-limit fallback fired (see
    agent.py's _call_llm). Token counts and `estimated_cost_usd` are None
    when the serving provider didn't report usage (see LLMResponse) or the
    model isn't in app.ai.providers.pricing's table — never a fabricated
    guess presented as real.
    """

    model: str = ""
    provider: str = ""
    prompt: str = ""
    raw_response: str = ""
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None


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
    # The confirmed subset of `repositories_consulted` this work is actually
    # about — Context Discovery's explicit/selected repositories (see
    # `reasoning.projection.build_result`'s `selected_repositories`), not
    # merely every repository the graph traversal happened to touch.
    # Development/Testing/Engineering Review read this to scope their own
    # work to what's confirmed rather than everything consulted for context.
    target_repositories: list[str] = Field(default_factory=list)
    blueprint: dict[str, Any] | None = Field(default=None)
    prompt_version: str = "1.0"

    # Deterministic warnings the agent code produced itself (entity/tenant
    # mismatches, claims not backed by this run's own tool evidence) — never
    # LLM-generated, never silently dropped. Empty when nothing was flagged.
    # See app.agents.verification.
    verification_warnings: list[str] = Field(default_factory=list)

    # Structured counterpart to verification_warnings above, populated only
    # by app.agents.component_grounding.check_test_used_as_production — a
    # claim (test class named as production code) that was rejected or
    # replaced, with enough structure for the UI to render distinctly and
    # for a future automated gate to act on `warning_type` without parsing
    # prose. Additive: existing readers of verification_warnings are
    # unaffected; this is empty whenever nothing was rejected/replaced.
    component_warnings: list[ComponentWarning] = Field(default_factory=list)

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
