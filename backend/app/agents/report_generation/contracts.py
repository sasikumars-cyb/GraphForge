"""Report V2 contract types — the `ReportViewModel` vocabulary.

This module defines only the shared enums and the small, flat data
carriers Phase 1 (data plumbing) populates. It deliberately does NOT
define the full nested `ReportViewModel` (HeaderVM, ScopeVM,
ArchitectureVM, ...) — assembling those from this phase's normalized data
is Phase 2's job (the deterministic view-model builder). Phase 1 produces
inputs Phase 2 consumes; it does not itself decide report structure.

Every enum value here is set by a `data_plumbing.py` mapping function
that documents its exact backend source in its own docstring — see that
module. Nothing in this file is ever set by an LLM call (see the Report
V2 design's "deterministic vs LLM-generated" boundary).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Readiness(StrEnum):
    READY = "ready"
    NEEDS_REVISION = "needs_revision"
    NOT_READY = "not_ready"
    UNKNOWN = "unknown"  # Engineering Review never ran


class SynthesisStatus(StrEnum):
    """LLM-reasoning classification — answers 'does GraphForge's own
    reasoning support this', never 'was this code-checked'. Kept
    independent of VerificationStatus by design (Report V2 design doc,
    point 4/2) — never merge the two into one symbol."""

    SUPPORTED = "supported"
    INFERRED = "inferred"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class SynthesisRunState(StrEnum):
    """Whether reasoning *execution itself* succeeded, at the scope of the
    whole Hypotheses/Contradictions section — never a per-claim belief
    (that's `SynthesisStatus` above). Mirrors the plain string literal
    `app.context_pipeline.reasoning.projection._SynthesisRunState`
    (`"not_run"`/`"failed"`/`"completed_empty"`/`"completed"`) one-to-one;
    kept as a separate type rather than importing that Literal here so
    report_generation depends on context_pipeline's *output*, never the
    reverse — see `data_plumbing.map_synthesis_run_state`, the one place
    that bridges the two, same pattern as `map_synthesis_status` already
    bridges `Hypothesis.status` into this enum's sibling.

    ADR 0024 §11 is the source of truth for what each value means and how
    it maps to `Availability`/UI copy. `COMPLETED` is the exhaustive
    fourth state beyond the three degraded-state cases the design names
    (`NOT_RUN`/`FAILED`/`COMPLETED_EMPTY`) — the "succeeded and produced
    real items" case, which is the common path and must never be confused
    with `COMPLETED_EMPTY`.
    """

    NOT_RUN = "not_run"
    FAILED = "failed"
    COMPLETED_EMPTY = "completed_empty"
    COMPLETED = "completed"


class VerificationStatus(StrEnum):
    """Deterministic, code-checked classification — never set by an LLM.
    Independent of SynthesisStatus by design."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    NOT_CHECKED = "not_checked"


class Availability(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class FileRole(StrEnum):
    MODIFIED = "modified"
    CONSULTED = "consulted"
    DEPENDENCY = "dependency"
    PROPOSED_UNVERIFIED = "proposed_unverified"


class RiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNSPECIFIED = "unspecified"  # the source gave no severity at all


@dataclass(frozen=True)
class SectionAvailability:
    status: Availability
    reason: str | None = None  # required (non-None) whenever status != AVAILABLE

    def __post_init__(self) -> None:
        if self.status != Availability.AVAILABLE and not self.reason:
            raise ValueError(
                f"SectionAvailability({self.status}) requires a non-empty reason — "
                "a degraded/unavailable section must always be able to say why."
            )


@dataclass(frozen=True)
class LedgerRow:
    """One row of the two-axis Knowledge Ledger (Report V2 design, point
    2 correction) — `synthesis_status` and `verification_status` are
    independent; a row may populate one, the other, both, or neither.
    Never flattened into a 'confirmed'/'unresolved' bucket."""

    claim: str
    source_stage: str
    source_field: str
    synthesis_status: SynthesisStatus | None
    verification_status: VerificationStatus | None


class SubjectEntityKind(StrEnum):
    REPOSITORY = "repository"
    FILE = "file"
    COMPONENT = "component"


@dataclass(frozen=True)
class SubjectEntity:
    """A hypothesis's structured, exact-match-only claim subject — ADR
    0025 §7/§8. Mirrors `app.context_pipeline.reasoning.understanding.
    HypothesisSubjectEntity` one-to-one, kept as a separate type for the
    same reason `SynthesisRunState` mirrors its own upstream Literal
    rather than importing it (report_generation depends on context_
    pipeline's *output*, never the reverse)."""

    kind: SubjectEntityKind
    name: str


@dataclass(frozen=True)
class HypothesisEntry:
    """One hypothesis from Context Discovery's
    `reasoning_summary.hypotheses[]` (app.context_pipeline.reasoning.
    projection.build_reasoning_summary) — a straight projection of the
    real `Hypothesis` model, never reshaped. `supporting_evidence`/
    `contradicting_evidence` are prose (see that model's own fields) —
    NOT stable Evidence IDs; a report renders them as text, never as a
    graph edge to a specific Evidence item, because no such stable
    reference exists in the source data (Report V2 design, point 3).

    `subject_entity` is `None` for the vast majority of real hypotheses
    (see `HypothesisSubjectEntity`'s own docstring for exactly when it's
    set) — it is the one field ADR 0025's correlation pass
    (`map_verification_status_for_subject_entity`) reads to ever produce
    anything other than `NOT_CHECKED`."""

    statement: str
    status: SynthesisStatus
    confidence: float
    supporting_evidence: list[str]
    contradicting_evidence: list[str]
    subject_entity: SubjectEntity | None = None


@dataclass(frozen=True)
class ContradictionEntry:
    """One contradiction from Context Discovery's
    `reasoning_summary.contradictions[]` — a straight projection of the
    real `Contradiction` model. `evidence_for`/`evidence_against` are
    prose, same caveat as `HypothesisEntry`."""

    statement: str
    evidence_for: list[str]
    evidence_against: list[str]
    resolved: bool
    resolution_note: str


@dataclass(frozen=True)
class TimelineEntry:
    cycle: int
    provider: str
    action: str
    outcome: str
    summary: str
    intent: str


@dataclass(frozen=True)
class EvidenceCategoryCount:
    kind: str
    count: int


@dataclass(frozen=True)
class ConfidenceStagePoint:
    stage: str
    label: str
    confidence: float | None
    delta_from_previous: float | None
    dropped: bool


@dataclass(frozen=True)
class ConfidenceJourney:
    points: list[ConfidenceStagePoint]
    summary_sentence: str


@dataclass(frozen=True)
class ArchitectureDiagramRef:
    diagram_id: str
    title: str
    grounded: bool
    grounded_label: str
    source_stage: str


@dataclass(frozen=True)
class ScopeFileEntry:
    path: str
    repository: str
    role: FileRole
    description: str | None


@dataclass(frozen=True)
class RiskEntry:
    description: str
    severity: RiskSeverity
    mitigated: bool | None  # None = no Engineering Review judgment exists
    mitigation_text: str | None
    source_stage: str


class OpenItemKind(StrEnum):
    """Where an open item came from — so the same item can be counted once,
    in one list, and still be explained by section. Introduced with the
    "one source of truth for blocking/advisory" rule (see
    `view_model.build_open_items`): every section that mentions an open or
    blocking item reads that one list, never its own re-derivation."""

    BLOCKING_ISSUE = "blocking_issue"  # Engineering Review's own blocking_issues[]
    UNRESOLVED_CONTRADICTION = "unresolved_contradiction"
    KNOWLEDGE_GAP = "knowledge_gap"


@dataclass(frozen=True)
class OpenQuestionEntry:
    text: str
    source_stage: str
    is_blocking: bool
    kind: OpenItemKind = OpenItemKind.KNOWLEDGE_GAP


@dataclass(frozen=True)
class ConfirmedFinding:
    """Something the investigation actually established — never a
    hypothesis. Only two sources qualify (see `view_model._build_findings`):
    a Context Discovery fact whose own `verified` flag is True, and a
    Knowledge Ledger row whose `verification_status` is VERIFIED. A
    supported-but-unverified hypothesis is NOT a confirmed finding, however
    high its confidence — that separation is the whole point of this type."""

    statement: str
    source_stage: str
    source_field: str
    evidence_summary: str | None = None
