"""Report V2 Phase 2 — the deterministic `ReportViewModel` builder.

ADR 0024 is the source of truth for every decision in this module; each
dataclass/function below cites the ADR section it implements. This is the
one new assembly layer Phase 2 adds — it calls *only* the existing Phase 1
`data_plumbing.py` functions (never re-derives a value they already
compute) and performs no LLM call and no rendering. `report_generation/
agent.py` is the only caller: it builds this view model, hands it to the
one permitted LLM call (the `executive_summary` paragraph — ADR §13), then
serializes the whole thing for the frontend to render.

Architectural rule, stated once here because it governs every function in
this file: the LLM never decides *whether or how* structured reasoning
appears. Every field except `ReportViewModel.executive_summary` is a pure
function of already-persisted, already-mapped data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from app.agents.git_ops._artifact_reader import StageStepData
from app.agents.report_generation import data_plumbing as dp
from app.agents.report_generation.contracts import (
    Availability,
    ConfidenceStagePoint,
    ContradictionEntry,
    EvidenceCategoryCount,
    HypothesisEntry,
    LedgerRow,
    OpenQuestionEntry,
    Readiness,
    SectionAvailability,
    SynthesisRunState,
    TimelineEntry,
)

if TYPE_CHECKING:
    from app.models.workflow import Workflow

# Scale rules — ADR 0024 §12. View-model constants, not hardcoded in JSX,
# so they're one place to tune and one place to test.
_MAX_TIMELINE_ROWS = 8
_MAX_HYPOTHESIS_CARDS = 6
_MAX_KNOWLEDGE_ITEMS = 8


@dataclass(frozen=True)
class HeaderVM:
    question: str
    workflow_title: str
    repository: str | None
    readiness: Readiness
    generated_at: str


@dataclass(frozen=True)
class ConfidenceSectionVM:
    availability: SectionAvailability
    current: float | None
    points: list[ConfidenceStagePoint]
    summary_sentence: str


@dataclass(frozen=True)
class TimelineSectionVM:
    availability: SectionAvailability
    steps: list[TimelineEntry]
    truncated_count: int


@dataclass(frozen=True)
class KnowledgeSectionVM:
    availability: SectionAvailability
    known: list[str]
    known_truncated_count: int
    unknown: list[str]
    unknown_truncated_count: int


@dataclass(frozen=True)
class HypothesisVM:
    entry: HypothesisEntry
    verification_status: str | None  # VerificationStatus.value, or None -> render as NOT_CHECKED


@dataclass(frozen=True)
class HypothesesSectionVM:
    availability: SectionAvailability
    synthesis_state: SynthesisRunState
    items: list[HypothesisVM]
    truncated_count: int


@dataclass(frozen=True)
class ContradictionsSectionVM:
    availability: SectionAvailability
    synthesis_state: SynthesisRunState
    items: list[ContradictionEntry]


@dataclass(frozen=True)
class EvidenceSectionVM:
    availability: SectionAvailability
    categories: list[EvidenceCategoryCount]
    total: int


@dataclass(frozen=True)
class NextActionsSectionVM:
    availability: SectionAvailability
    questions: list[OpenQuestionEntry]


@dataclass(frozen=True)
class ReportViewModel:
    header: HeaderVM
    confidence: ConfidenceSectionVM
    timeline: TimelineSectionVM
    knowledge: KnowledgeSectionVM
    hypotheses: HypothesesSectionVM
    contradictions: ContradictionsSectionVM
    evidence: EvidenceSectionVM
    next_actions: NextActionsSectionVM
    # The one LLM-authored field in the whole model (ADR §13) — narrates
    # what's already decided above, never adds a fact absent from it.
    # None when the summary call wasn't attempted or failed; every other
    # section renders correctly with or without it.
    executive_summary: str | None = None


def _repository_name(context_discovery_bundle: StageStepData | None) -> str | None:
    """Source: `context_discovery.result["repositories"]` — the first
    `selected=True` entry, falling back to the first entry, falling back
    to the legacy `ranked_repository_names[0]` for a result predating the
    `repositories` model (ADR 0010 §2, same fallback `ContextDiscoveryResult`
    itself documents). `None` when Context Discovery never ran or named no
    repository — never a guess."""
    if context_discovery_bundle is None:
        return None
    repos = context_discovery_bundle.result.get("repositories") or []
    for repo in repos:
        if repo.get("selected"):
            name = repo.get("name")
            return str(name) if name else None
    if repos:
        name = repos[0].get("name")
        return str(name) if name else None
    ranked = context_discovery_bundle.result.get("ranked_repository_names") or []
    return str(ranked[0]) if ranked else None


def _build_header(workflow: Workflow, bundles: dict[str, StageStepData | None]) -> HeaderVM:
    cd_bundle = bundles.get("context_discovery")
    question = (cd_bundle.result.get("original_request") if cd_bundle else None) or str(
        workflow.original_prompt
    )
    return HeaderVM(
        question=question,
        workflow_title=workflow.title,
        repository=_repository_name(cd_bundle),
        readiness=dp.map_readiness(bundles.get("engineering_review")),
        generated_at=workflow.updated_at.isoformat() if workflow.updated_at else "",
    )


def _build_confidence(bundles: dict[str, StageStepData | None]) -> ConfidenceSectionVM:
    """ADR §6: a straight call to Phase 1's `map_confidence_journey`, no
    re-derivation. `current` is the last stage's own score, not an
    average — the same "what does the reader see right now" value the
    live workflow UI already shows."""
    journey = dp.map_confidence_journey(bundles)
    current = next(
        (p.confidence for p in reversed(journey.points) if p.confidence is not None), None
    )
    availability = (
        SectionAvailability(Availability.AVAILABLE)
        if any(p.confidence is not None for p in journey.points)
        else SectionAvailability(
            Availability.UNAVAILABLE, reason="No stage produced a confidence score yet."
        )
    )
    return ConfidenceSectionVM(
        availability=availability,
        current=current,
        points=journey.points,
        summary_sentence=journey.summary_sentence,
    )


def _build_timeline(bundles: dict[str, StageStepData | None]) -> TimelineSectionVM:
    """ADR §10/§12: capped at `_MAX_TIMELINE_ROWS`, sorted by iteration —
    never the full investigation trail past that cap, and never raw
    evidence/graph nodes (the source, `discovery_report.investigation`, is
    already a bounded, curated timeline, not a graph dump)."""
    steps, availability = dp.map_investigation_timeline(bundles.get("context_discovery"))
    steps = sorted(steps, key=lambda s: s.cycle)
    visible = steps[:_MAX_TIMELINE_ROWS]
    return TimelineSectionVM(
        availability=availability,
        steps=visible,
        truncated_count=max(0, len(steps) - len(visible)),
    )


def _build_knowledge(bundles: dict[str, StageStepData | None]) -> KnowledgeSectionVM:
    """ADR §6: a thin dict-read over `discovery_report.findings[]`/`gaps[]`
    — both real, persisted, bounded lists (Phase 1's `build_discovery_
    report`), never wrapped in their own `map_*` function because Phase 1
    only scoped the hypothesis/ledger/timeline gap explicitly. "Known" is
    one short statement per verified finding group; "unknown" is each open
    gap's own `summary` — never re-derived text."""
    cd_bundle = bundles.get("context_discovery")
    if cd_bundle is None:
        return KnowledgeSectionVM(
            availability=SectionAvailability(
                Availability.UNAVAILABLE,
                reason="Context Discovery did not complete for this workflow.",
            ),
            known=[],
            known_truncated_count=0,
            unknown=[],
            unknown_truncated_count=0,
        )
    report = cd_bundle.result.get("discovery_report") or {}
    known_all: list[str] = []
    for group in report.get("findings") or []:
        kind = str(group.get("kind") or "finding")
        total = int(group.get("total") or len(group.get("items") or []))
        if total <= 0:
            continue
        verified_count = sum(1 for item in group.get("items") or [] if item.get("verified"))
        label = kind.replace("_", " ")
        plural = "s" if total != 1 else ""
        if verified_count:
            known_all.append(f"{total} {label}{plural} recorded, {verified_count} verified.")
        else:
            known_all.append(f"{total} {label}{plural} recorded.")
    unknown_all = [
        str(gap.get("summary") or gap.get("gap_id") or "")
        for gap in report.get("gaps") or []
        if gap.get("status") in ("open", "claimed", "unresolvable")
    ]
    known_visible = known_all[:_MAX_KNOWLEDGE_ITEMS]
    unknown_visible = unknown_all[:_MAX_KNOWLEDGE_ITEMS]
    return KnowledgeSectionVM(
        availability=SectionAvailability(Availability.AVAILABLE),
        known=known_visible,
        known_truncated_count=max(0, len(known_all) - len(known_visible)),
        unknown=unknown_visible,
        unknown_truncated_count=max(0, len(unknown_all) - len(unknown_visible)),
    )


def _build_hypotheses(
    bundles: dict[str, StageStepData | None], ledger_rows: list[LedgerRow]
) -> HypothesesSectionVM:
    """ADR 0024 §6/§8: `map_hypotheses` for the entries; each hypothesis's
    `verification_status` is looked up by matching a Knowledge Ledger
    row's `source_field` (`reasoning_summary.hypotheses[i]`) — a
    positional match (see `TestBuildHypotheses::
    test_correlation_mechanism_works_if_a_matching_ledger_row_ever_exists`
    in test_report_view_model.py).

    **Updated for ADR 0025 (Phase 3):** this lookup now has a real
    caller. `map_knowledge_ledger_rows` correlates a hypothesis-sourced
    row's `verification_status` via `map_verification_status_for_
    subject_entity` whenever the hypothesis carries a claim-type-gated,
    exact-match `subject_entity` — see that function's own docstring
    (data_plumbing.py) for the precise, tested condition (ADR 0025 §8/
    §9a). Most hypotheses still correctly resolve to `NOT_CHECKED` — a
    hypothesis with no `subject_entity`, or one that doesn't exactly
    match a real check, never correlates
    (`TestHypothesesWithoutSubjectEntityNeverCorrelate` in
    test_report_view_model.py) — but `SUPPORTED+VERIFIED`/`SUPPORTED+
    UNVERIFIED` are no longer structurally impossible, proven against a
    real live workflow (see the Phase 3 release report).

    Sorted by confidence, descending (§8 — "why does it believe the
    strongest hypothesis" answered by position), capped at
    `_MAX_HYPOTHESIS_CARDS`.
    """
    entries, availability, run_state = dp.map_hypotheses(bundles.get("context_discovery"))
    verification_by_index: dict[int, str] = {}
    for row in ledger_rows:
        if row.source_stage == "context_discovery" and row.source_field.startswith(
            "reasoning_summary.hypotheses["
        ):
            idx_str = row.source_field.split("[", 1)[1].rstrip("]")
            if idx_str.isdigit() and row.verification_status is not None:
                verification_by_index[int(idx_str)] = row.verification_status.value

    indexed = list(enumerate(entries))
    indexed.sort(key=lambda pair: pair[1].confidence, reverse=True)
    visible = indexed[:_MAX_HYPOTHESIS_CARDS]
    items = [
        HypothesisVM(entry=entry, verification_status=verification_by_index.get(i))
        for i, entry in visible
    ]
    return HypothesesSectionVM(
        availability=availability,
        synthesis_state=run_state,
        items=items,
        truncated_count=max(0, len(entries) - len(items)),
    )


def _build_contradictions(bundles: dict[str, StageStepData | None]) -> ContradictionsSectionVM:
    entries, availability, run_state = dp.map_contradictions(bundles.get("context_discovery"))
    return ContradictionsSectionVM(
        availability=availability, synthesis_state=run_state, items=list(entries)
    )


def _build_evidence(bundles: dict[str, StageStepData | None]) -> EvidenceSectionVM:
    """ADR §6/§12: `map_evidence_summary` over Context Discovery's own
    `AgentStep.evidence` — bounded by construction (at most 5 kinds),
    category counts only, never a per-item list on this page."""
    cd_bundle = bundles.get("context_discovery")
    if cd_bundle is None:
        return EvidenceSectionVM(
            availability=SectionAvailability(
                Availability.UNAVAILABLE,
                reason="Context Discovery did not complete for this workflow.",
            ),
            categories=[],
            total=0,
        )
    categories = dp.map_evidence_summary(cd_bundle.evidence)
    total = sum(c.count for c in categories)
    availability = (
        SectionAvailability(Availability.AVAILABLE)
        if categories
        else SectionAvailability(
            Availability.DEGRADED, reason="Context Discovery recorded no evidence trail."
        )
    )
    return EvidenceSectionVM(availability=availability, categories=categories, total=total)


def _build_next_actions(bundles: dict[str, StageStepData | None]) -> NextActionsSectionVM:
    questions = dp.map_open_questions(
        bundles.get("context_discovery"), bundles.get("engineering_review")
    )
    availability = (
        SectionAvailability(Availability.AVAILABLE)
        if questions
        else SectionAvailability(
            Availability.AVAILABLE
        )  # an empty "what's next" is a real, positive outcome — nothing blocking
    )
    return NextActionsSectionVM(availability=availability, questions=questions)


def build_report_view_model(
    workflow: Workflow, bundles: dict[str, StageStepData | None]
) -> ReportViewModel:
    """The single entry point `report_generation/agent.py` calls. Pure and
    deterministic — no LLM call, no I/O beyond what `bundles` already
    carries (fetched once via `fetch_all_stage_bundles`). `executive_
    summary` is left `None` here; the agent fills it in afterward from
    this already-built model (ADR §13), never before."""
    ledger_rows = dp.map_knowledge_ledger_rows(
        bundles.get("planning"),
        bundles.get("development"),
        bundles.get("testing"),
        context_discovery_bundle=bundles.get("context_discovery"),
    )
    return ReportViewModel(
        header=_build_header(workflow, bundles),
        confidence=_build_confidence(bundles),
        timeline=_build_timeline(bundles),
        knowledge=_build_knowledge(bundles),
        hypotheses=_build_hypotheses(bundles, ledger_rows),
        contradictions=_build_contradictions(bundles),
        evidence=_build_evidence(bundles),
        next_actions=_build_next_actions(bundles),
    )


def to_json_dict(model: ReportViewModel) -> dict[str, Any]:
    """Serializes a `ReportViewModel` for `workflow_reports.view_model`
    (JSON column) and for the API response the frontend fetches. Plain
    `dataclasses.asdict` — every enum here (`Readiness`, `Availability`,
    `SynthesisRunState`, `SynthesisStatus`, `VerificationStatus`) is a
    `StrEnum`, itself a `str` subclass, so it round-trips through
    `json.dumps` as its plain string value with no custom encoder needed;
    this function exists as a named, tested seam rather than callers each
    reaching for `dataclasses.asdict` directly."""
    return asdict(model)
