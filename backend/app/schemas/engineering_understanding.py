"""Engineering Understanding DTOs and projection input.

Output DTOs describe the API response shape for the Context Explorer.
``ProjectionInput`` describes the typed input contract for the mapper.

These are presentation models only — never persisted, never stored.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.context_pipeline.reasoning.curation import EvidencePackage
from app.context_pipeline.reasoning.understanding import EngineeringUnderstanding

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

    # Extracted from ContextDiscoveryResult by caller
    original_request: str = ""
    readiness: Readiness = "BLOCKED"
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
