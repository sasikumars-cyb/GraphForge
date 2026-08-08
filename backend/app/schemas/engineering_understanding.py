"""Engineering Understanding DTOs and projection input.

Output DTOs describe the API response shape for the Context Explorer.
``ProjectionInput`` describes the typed input contract for the mapper.

These are presentation models only — never persisted, never stored.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.context_pipeline.reasoning.curation import EvidencePackage
from app.context_pipeline.reasoning.memory import CompletionStatus
from app.context_pipeline.reasoning.understanding import (
    EngineeringUnderstanding,
    InvestigationWorkspace,
)

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

Readiness = Literal["READY", "PARTIAL", "BLOCKED"]

Capability = Literal[
    "code_understanding",
    "architecture",
    "documentation",
    "test_coverage",
    "dependency_analysis",
]

# ---------------------------------------------------------------------------
# Output sub-models
# ---------------------------------------------------------------------------


class RepositorySummaryDTO(BaseModel):
    """Repository ownership summary."""

    primary: str = ""
    supporting: list[str] = Field(default_factory=list)
    ownership: list[str] = Field(default_factory=list)


class AreaClusterDTO(BaseModel):
    """One logical area grouping related components."""

    name: str
    components: list[str] = Field(default_factory=list)


class UnknownItemDTO(BaseModel):
    """One item in the unknowns list, categorised."""

    category: Literal["known", "unknown", "unavailable"]
    description: str


class PlanningFactorDTO(BaseModel):
    """One factor in the planning assessment checklist."""

    satisfied: bool
    description: str


class PlanningAssessmentDTO(BaseModel):
    """Planning readiness verdict with explanation."""

    status: Readiness
    reasons: list[PlanningFactorDTO] = Field(default_factory=list)


class HypothesisDTO(BaseModel):
    """One competing explanation the synthesis LLM considered — see
    `reasoning.understanding.Hypothesis`, the domain model this presentation
    shape is projected from (never duplicated logic, only re-shaped for
    display: `id` and `is_strongest` are added, nothing else changes)."""

    id: str
    description: str
    status: Literal["supported", "rejected", "unknown"]
    confidence: float
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    # The single highest-confidence non-rejected hypothesis, at most one
    # `True` across the whole list — computed by the mapper, never by the
    # LLM (an LLM self-declaring its own leading hypothesis is exactly the
    # kind of unearned assertion this whole system is built to avoid).
    is_strongest: bool = False


class ContradictionDTO(BaseModel):
    """One conflict the synthesis LLM flagged rather than silently averaged
    away — see `reasoning.understanding.Contradiction`."""

    id: str
    description: str
    evidence_for: list[str] = Field(default_factory=list)
    evidence_against: list[str] = Field(default_factory=list)
    resolved: bool = False
    resolution_note: str = ""


class NextInvestigationDTO(BaseModel):
    """One capability the engine's own priority boost currently favors —
    see `reasoning.understanding.capability_priority` /
    `reasoning.investigation_planner.plan_priority_boost`, the exact
    deterministic signal `engine._select` reads to break ties. This is a
    read of what already happened (the boost that was live when this
    investigation last reasoned), not a live recomputation."""

    capability: str
    label: str
    priority: float


class ReasoningSummaryDTO(BaseModel):
    """Projection of `reasoning.understanding.InvestigationWorkspace` (plus
    the sibling `investigation_priority` boost) for the Context Explorer's
    Reasoning view. `has_reasoning=False` means the synthesis LLM produced
    no hypotheses or contradictions this run (e.g. a request with nothing
    to investigate) — genuinely different from `degraded=True`, which means
    synthesis *ran* but the call itself failed or returned something
    invalid and the engine fell back to a deterministic, evidence-only
    summary (see `understanding._deterministic_understanding`)."""

    has_reasoning: bool = False
    degraded: bool = False
    hypotheses: list[HypothesisDTO] = Field(default_factory=list)
    contradictions: list[ContradictionDTO] = Field(default_factory=list)
    open_contradiction_count: int = 0
    resolved_contradiction_count: int = 0
    strongest_hypothesis_id: str | None = None
    dead_ends: list[str] = Field(default_factory=list)
    next_investigation: list[NextInvestigationDTO] = Field(default_factory=list)
    # The most recent entry of `InvestigationWorkspace.investigation_
    # history` — code-authored, factual narration ("Cycle 3: re-synthesized
    # over 12 evidence record(s) — ..."), never LLM prose. One line, so the
    # Reasoning section can show "as of" context without repeating the full
    # history inline.
    last_update: str = ""


class DebugBundleDTO(BaseModel):
    """Execution internals — only populated when ``debug=true``."""

    investigation_trail: list[dict[str, Any]] = Field(default_factory=list)
    confidence_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    graph_components: list[dict[str, Any]] = Field(default_factory=list)
    graph_topics: list[dict[str, Any]] = Field(default_factory=list)
    repository_ranking: list[str] = Field(default_factory=list)
    capability_confidence: dict[str, float] = Field(default_factory=dict)
    planning_metadata: dict[str, Any] = Field(default_factory=dict)
    working_memory: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    evidence_package_raw: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Top-level output DTO
# ---------------------------------------------------------------------------


class EngineeringUnderstandingDTO(BaseModel):
    """Presentation model for the Context Explorer — never persisted."""

    business_goal: str = ""
    current_situation: str = ""
    expected_outcome: str = ""
    repository_summary: RepositorySummaryDTO = Field(
        default_factory=RepositorySummaryDTO,
    )
    architecture_summary: str = ""
    relevant_areas: list[AreaClusterDTO] = Field(default_factory=list)
    known_constraints: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    unknowns: list[UnknownItemDTO] = Field(default_factory=list)
    evidence_summary: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    planning_assessment: PlanningAssessmentDTO = Field(
        default_factory=lambda: PlanningAssessmentDTO(status="BLOCKED"),
    )
    confidence_explanation: str = ""
    documentation_status: str = ""
    next_step: str = ""
    # Why investigation stopped — an axis distinct from `planning_assessment.
    # status` (readiness: is there enough) — see `reasoning.memory.
    # WorkingContext.completion_status`. Defaults to "PARTIAL", the same
    # conservative default the underlying persisted field uses for a result
    # written before this field existed.
    completion_status: CompletionStatus = "PARTIAL"
    reasoning_summary: ReasoningSummaryDTO = Field(default_factory=ReasoningSummaryDTO)
    debug_bundle: DebugBundleDTO | None = None


# ---------------------------------------------------------------------------
# Mapper input models — typed graph projections
# ---------------------------------------------------------------------------


class TopicProjection(BaseModel):
    """Typed projection of a graph topic node.

    The caller extracts these from the graph subsystem's untyped dicts.
    The mapper never calls ``.get()`` or inspects dictionary keys.
    """

    name: str


class ComponentProjection(BaseModel):
    """Typed projection of a graph component node.

    The caller extracts these from the graph subsystem's untyped dicts.
    The mapper never calls ``.get()`` or inspects dictionary keys.
    """

    name: str
    topic: str = ""


class CapabilityFactor(BaseModel):
    """Pre-computed capability assessment result.

    Reusable typed representation of a single capability's readiness.
    The ``satisfied`` boolean is the domain-computed value from
    ``CapabilityAssessment.satisfied`` — never re-derived by the mapper.
    """

    capability: str
    label: str
    satisfied: bool


class ProjectionInput(BaseModel):
    """Typed input for the engineering understanding mapper.

    The caller (API endpoint) parses persisted data into this shape.
    The mapper receives only typed models — it never touches raw JSON,
    dicts from storage, or untyped data.
    """

    # Pre-parsed domain models
    understanding: EngineeringUnderstanding
    evidence_package: EvidencePackage
    # The synthesis LLM's own scratch reasoning — hypotheses, contradictions,
    # dead ends. Parsed by the caller from `working_memory.derived.
    # investigation_workspace` (see `understanding.synthesize_engineering_
    # understanding`'s docstring on why Planning never reads this but the
    # Context Explorer's Reasoning view now does). Reusing the existing
    # domain model directly, not a second copy of its shape.
    workspace: InvestigationWorkspace = Field(default_factory=InvestigationWorkspace)
    # `working_memory.derived.investigation_priority` — the same capability
    # boost dict `engine._select` reads at selection time.
    investigation_priority: dict[str, float] = Field(default_factory=dict)

    # Extracted from ContextDiscoveryResult by caller
    original_request: str = ""
    readiness: Readiness = "BLOCKED"
    completion_status: CompletionStatus = "PARTIAL"
    blocking_reasons: list[str] = Field(default_factory=list)
    graph_topics: list[TopicProjection] = Field(default_factory=list)
    graph_components: list[ComponentProjection] = Field(default_factory=list)

    # Extracted from discovery_report.confidence_breakdown by caller
    capability_factors: list[CapabilityFactor] = Field(default_factory=list)

    # Extracted from discovery_report.gaps by caller
    gap_summaries: list[str] = Field(default_factory=list)
    unavailable_gaps: list[str] = Field(default_factory=list)

    # Pre-computed presentation fields (caller derives, mapper projects)
    documentation_status: str = ""
    next_step: str = ""

    # Pre-built debug data (only populated when debug requested)
    debug_bundle: DebugBundleDTO | None = None
