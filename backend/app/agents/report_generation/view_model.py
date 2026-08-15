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
    ConfirmedFinding,
    ContradictionEntry,
    EvidenceCategoryCount,
    HypothesisEntry,
    LedgerRow,
    OpenItemKind,
    OpenQuestionEntry,
    Readiness,
    SectionAvailability,
    SynthesisRunState,
    SynthesisStatus,
    TimelineEntry,
    VerificationStatus,
)

if TYPE_CHECKING:
    from app.models.workflow import Workflow

# Scale rules — ADR 0024 §12. View-model constants, not hardcoded in JSX,
# so they're one place to tune and one place to test.
_MAX_TIMELINE_ROWS = 8
_MAX_HYPOTHESIS_CARDS = 6
_MAX_KNOWLEDGE_ITEMS = 8
_MAX_CONFIRMED_FINDINGS = 8

# How far the strongest hypothesis's own confidence may sit from the
# investigation's overall confidence before the report states the gap
# explicitly rather than leaving a reader to notice two different numbers
# and assume one of them is wrong. Expressed as an absolute difference in
# the same 0..1 scale both values already use.
_CONFIDENCE_DIVERGENCE_THRESHOLD = 0.2


@dataclass(frozen=True)
class HeaderVM:
    question: str
    workflow_title: str
    repository: str | None
    # The readiness the whole report renders — Engineering Review's own
    # verdict, downgraded when open blocking items contradict it (see
    # `_derive_readiness`). Every section reads this one value, so the
    # badge, the outcome, and the recommendation can never disagree.
    readiness: Readiness
    # Engineering Review's raw, un-downgraded `readiness_status`, kept for
    # traceability: when `readiness != reported_readiness`, the outcome
    # section states that the downgrade happened and why.
    reported_readiness: Readiness
    generated_at: str


@dataclass(frozen=True)
class ConfidenceBreakdownVM:
    """The two confidence numbers a report carries, never merged and never
    presented as comparable without saying what each one measures.

    `top_hypothesis_confidence` answers "how sure are we about this
    specific candidate explanation"; `overall` answers "how sure are we
    that the issue is understood well enough to implement a fix". A
    95%-confident hypothesis inside a 45%-confident investigation is a
    coherent, common state — `divergence_note` is what says so out loud
    rather than leaving two bare percentages to look like a bug."""

    overall: float | None
    overall_label: str
    overall_basis: str
    top_hypothesis_confidence: float | None
    top_hypothesis_statement: str | None
    top_hypothesis_label: str
    divergence_note: str | None


@dataclass(frozen=True)
class ConfidenceSectionVM:
    availability: SectionAvailability
    current: float | None
    points: list[ConfidenceStagePoint]
    summary_sentence: str
    breakdown: ConfidenceBreakdownVM


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
class FindingsSectionVM:
    """[ Confirmed Findings ] — what the investigation actually established,
    kept structurally separate from `HypothesesSectionVM` (what is merely
    plausible). Nothing can appear in both: a hypothesis only becomes a
    confirmed finding if a deterministic check verified it, and that path
    produces a Knowledge Ledger row, not a hypothesis card."""

    availability: SectionAvailability
    items: list[ConfirmedFinding]
    truncated_count: int


@dataclass(frozen=True)
class ContradictionVM:
    """A contradiction plus the three things a reader needs beyond its two
    evidence lists: whether it blocks a decision, what it does to the
    conclusion, and what would settle it. All three are derived
    deterministically from the entry itself (see `_build_contradictions`) —
    no LLM, no per-Jira special-casing."""

    entry: ContradictionEntry
    is_blocking: bool
    impact: str
    required_resolution: str


@dataclass(frozen=True)
class ContradictionsSectionVM:
    availability: SectionAvailability
    synthesis_state: SynthesisRunState
    items: list[ContradictionVM]


@dataclass(frozen=True)
class EvidenceSectionVM:
    availability: SectionAvailability
    categories: list[EvidenceCategoryCount]
    total: int


@dataclass(frozen=True)
class NextActionsSectionVM:
    availability: SectionAvailability
    questions: list[OpenQuestionEntry]
    # Counted here, once, from `questions` itself — every surface that
    # states "N blocking, M advisory" reads these fields instead of
    # re-filtering its own copy of the list (the defect that let one
    # section say "one open/blocking item" while another said "0
    # blocking, 1 advisory").
    blocking_count: int
    advisory_count: int


@dataclass(frozen=True)
class ReviewOutcomeVM:
    """[ Engineering Review Outcome ] — the decision the post-review
    document exists to communicate, in words, with its reasons and an
    actionable recommendation. Derived from the same readiness value and
    the same open-item list every other section reads; never a raw
    `needs_revision` string handed to the UI to label."""

    availability: SectionAvailability
    readiness: Readiness
    reported_readiness: Readiness
    outcome_label: str
    outcome_statement: str
    reasons: list[str]
    recommendation: str
    blocking_count: int
    advisory_count: int


@dataclass(frozen=True)
class ReportViewModel:
    header: HeaderVM
    review_outcome: ReviewOutcomeVM
    confidence: ConfidenceSectionVM
    timeline: TimelineSectionVM
    knowledge: KnowledgeSectionVM
    findings: FindingsSectionVM
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


def _build_header(
    workflow: Workflow,
    bundles: dict[str, StageStepData | None],
    readiness: Readiness,
    reported_readiness: Readiness,
) -> HeaderVM:
    cd_bundle = bundles.get("context_discovery")
    question = (cd_bundle.result.get("original_request") if cd_bundle else None) or str(
        workflow.original_prompt
    )
    return HeaderVM(
        question=question,
        workflow_title=workflow.title,
        repository=_repository_name(cd_bundle),
        readiness=readiness,
        reported_readiness=reported_readiness,
        generated_at=workflow.updated_at.isoformat() if workflow.updated_at else "",
    )


def _count(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def build_open_items(
    bundles: dict[str, StageStepData | None],
    contradictions: list[ContradictionVM],
) -> list[OpenQuestionEntry]:
    """THE list of open items for the whole report — the single source of
    truth requirement, implemented in one function.

    Two stage-sourced groups come straight from `dp.map_open_questions`
    (Engineering Review's `blocking_issues[]`, Context Discovery's open
    `gaps[]`); the third is added here: every unresolved contradiction is
    an open item, and a blocking one, because an unresolved contradiction
    means a conclusion the review would have to approve is not yet
    trustworthy. That promotion is the reason a report can never again say
    "one blocking item remains" in one section and "0 blocking" in
    another — both read this list, and nothing else counts independently.
    """
    items = list(
        dp.map_open_questions(bundles.get("context_discovery"), bundles.get("engineering_review"))
    )
    for c in contradictions:
        if not c.is_blocking:
            continue
        items.append(
            OpenQuestionEntry(
                text=f"Unresolved contradiction: {c.entry.statement}",
                source_stage="context_discovery",
                is_blocking=True,
                kind=OpenItemKind.UNRESOLVED_CONTRADICTION,
            )
        )
    return items


def _derive_readiness(reported: Readiness, open_items: list[OpenQuestionEntry]) -> Readiness:
    """The readiness the report actually renders. Normally Engineering
    Review's own verdict, passed through untouched. The one adjustment:
    a `READY` verdict with open blocking items is downgraded to
    `NEEDS_REVISION`, because a document that shows a blocking item and an
    approval at the same time is internally inconsistent, and of the two,
    the blocking item is the one backed by a specific, listed reason.

    `NOT_READY` is never upgraded, and `UNKNOWN` (Engineering Review never
    ran) is never turned into a verdict — this function only ever moves
    readiness in the conservative direction.
    """
    if reported == Readiness.READY and any(i.is_blocking for i in open_items):
        return Readiness.NEEDS_REVISION
    return reported


_OUTCOME_LABEL: dict[Readiness, str] = {
    Readiness.READY: "Approved",
    Readiness.NEEDS_REVISION: "Needs Revision",
    Readiness.NOT_READY: "Not Ready",
    Readiness.UNKNOWN: "Not Reviewed",
}

_OUTCOME_STATEMENT: dict[Readiness, str] = {
    Readiness.READY: (
        "Engineering Review approved this investigation — the proposed change is "
        "ready to implement as reviewed."
    ),
    Readiness.NEEDS_REVISION: (
        "Engineering Review did not approve implementation. The investigation must be "
        "revised and re-reviewed before a code change is made."
    ),
    Readiness.NOT_READY: (
        "Engineering Review found this investigation not ready for implementation."
    ),
    Readiness.UNKNOWN: (
        "Engineering Review has not run for this workflow, so no approval decision "
        "exists — nothing here should be read as an approval."
    ),
}


def _unconfirmed_top_hypothesis(hypotheses: HypothesesSectionVM) -> HypothesisVM | None:
    """The strongest hypothesis when it is *not* backed by a deterministic
    check — the case that must never be narrated as a root cause. `None`
    when there is no hypothesis at all, or when the strongest one really
    was verified (in which case it is also a confirmed finding and needs
    no caveat)."""
    if not hypotheses.items:
        return None
    top = hypotheses.items[0]
    if top.verification_status == VerificationStatus.VERIFIED.value:
        return None
    return top


def _live_hypothesis_count(hypotheses: HypothesesSectionVM) -> int:
    """Hypotheses still in play — anything the reasoning engine did not
    classify as contradicted. More than one means the investigation has
    not settled on a single explanation, which is what makes naming a file
    to change premature."""
    return (
        sum(1 for h in hypotheses.items if h.entry.status != SynthesisStatus.CONTRADICTED)
        + hypotheses.truncated_count
    )


def _outcome_reasons(
    readiness: Readiness,
    reported_readiness: Readiness,
    open_items: list[OpenQuestionEntry],
    contradictions: list[ContradictionVM],
    hypotheses: HypothesesSectionVM,
    findings: FindingsSectionVM,
) -> tuple[list[str], list[str]]:
    """Why the review landed where it did — assembled from real, already-
    decided state, in the order a reader needs it: what is established,
    what is not, what conflicts, what is still blocking.

    Every line is a template over a counted or copied value. No line is
    specific to any project, ticket, repository, or file — the same six
    rules produce the reasons for an approved single-hypothesis
    investigation and for a blocked, contradicted one alike.

    Returns `(reasons, concerns)`: `reasons` is the full list the outcome
    section renders; `concerns` is the subset that explains why readiness
    is not higher. They are built together rather than derived from each
    other so the confidence breakdown can cite a real concern ("the
    strongest explanation is unconfirmed") instead of accidentally
    justifying a low score with a positive finding.
    """
    established: list[str] = []
    concerns: list[str] = []

    if findings.items:
        total = len(findings.items) + findings.truncated_count
        established.append(
            f"{_count(total, 'finding')} {'was' if total == 1 else 'were'} confirmed by "
            "verified evidence and can be relied on."
        )

    top = _unconfirmed_top_hypothesis(hypotheses)
    if top is not None:
        concerns.append(
            f"The strongest candidate explanation ({round(top.entry.confidence * 100)}% "
            "confidence) is not confirmed: no deterministic check has verified it, so it "
            "remains a hypothesis rather than an established root cause."
        )

    live = _live_hypothesis_count(hypotheses)
    if live > 1:
        concerns.append(
            f"{_count(live, 'competing explanation')} are still in play — which one is "
            "correct has not been established."
        )

    unresolved = [c for c in contradictions if c.is_blocking]
    for c in unresolved:
        concerns.append(f"Unresolved contradiction in the evidence: {c.entry.statement}")

    gaps = [i for i in open_items if i.kind == OpenItemKind.KNOWLEDGE_GAP]
    if gaps:
        concerns.append(
            f"{_count(len(gaps), 'knowledge gap')} "
            f"{'remains' if len(gaps) == 1 else 'remain'} open and must be established "
            "before the conclusion can be trusted."
        )

    for item in open_items:
        if item.kind == OpenItemKind.BLOCKING_ISSUE:
            concerns.append(f"Engineering Review raised a blocking issue: {item.text}")

    if readiness != reported_readiness:
        concerns.append(
            f"Engineering Review reported '{reported_readiness.value}', but "
            f"{_count(sum(1 for i in open_items if i.is_blocking), 'blocking item')} "
            f"{'remains' if sum(1 for i in open_items if i.is_blocking) == 1 else 'remain'} "
            "open — this report renders the more conservative outcome."
        )

    if readiness == Readiness.UNKNOWN:
        concerns.append(
            "The Engineering Review stage did not complete, so no readiness verdict "
            "exists for this investigation."
        )

    if not established and not concerns:
        established.append(
            "No blocking items, unresolved contradictions, or open knowledge gaps were "
            "recorded against this investigation."
        )
    return established + concerns, concerns


def _build_recommendation(
    readiness: Readiness,
    open_items: list[OpenQuestionEntry],
    contradictions: list[ContradictionVM],
    hypotheses: HypothesesSectionVM,
) -> str:
    """The actionable half of the outcome: what to actually do next.

    The rule that matters most here — when more than one explanation is
    still in play, or the strongest one is unverified, the recommendation
    explicitly withholds "go change this file". A report that names an
    implementation target while a competing hypothesis says the cause is
    somewhere else is how a review gets acted on prematurely; the wording
    below is generated from those two structural facts, never from any
    knowledge of a specific ticket.
    """
    blocking = [i for i in open_items if i.is_blocking]
    advisory = [i for i in open_items if not i.is_blocking]

    if readiness == Readiness.READY:
        parts = ["Proceed with implementation as reviewed."]
        if advisory:
            parts.append(
                f"Track {_count(len(advisory), 'advisory item')} during implementation; "
                "none of them block the change."
            )
        return " ".join(parts)

    if readiness == Readiness.UNKNOWN:
        return (
            "Do not treat this as a reviewed decision. Run Engineering Review before "
            "acting on anything in this report."
        )

    parts = ["Do not implement the proposed change yet."]

    live = _live_hypothesis_count(hypotheses)
    top = _unconfirmed_top_hypothesis(hypotheses)
    if live > 1:
        parts.append(
            f"Validate which of the {live} competing explanations is correct before "
            "changing any code — this report deliberately does not nominate a file to "
            "modify while more than one explanation is in play."
        )
    elif top is not None:
        parts.append(
            "Confirm the leading explanation against the real system before changing "
            "code — it is supported by reasoning, not by a verified check."
        )

    if any(c.is_blocking for c in contradictions):
        parts.append(
            "Reconcile the unresolved contradiction(s) listed above; until they are "
            "settled the conclusion they touch cannot be approved."
        )

    if blocking:
        parts.append(
            f"Resolve {_count(len(blocking), 'blocking item')}, then re-run Engineering " "Review."
        )
    else:
        parts.append("Re-run Engineering Review once the above is addressed.")
    return " ".join(parts)


def _build_review_outcome(
    bundles: dict[str, StageStepData | None],
    readiness: Readiness,
    reported_readiness: Readiness,
    open_items: list[OpenQuestionEntry],
    contradictions: list[ContradictionVM],
    hypotheses: HypothesesSectionVM,
    findings: FindingsSectionVM,
) -> tuple[ReviewOutcomeVM, list[str]]:
    """Returns the outcome section plus its `concerns` list — the subset of
    reasons that explains why readiness is not higher, which the confidence
    breakdown cites so both are grounded in the same derivation."""
    er_bundle = bundles.get("engineering_review")
    availability = (
        SectionAvailability(Availability.AVAILABLE)
        if er_bundle is not None
        else SectionAvailability(
            Availability.UNAVAILABLE,
            reason="The Engineering Review stage did not complete for this workflow.",
        )
    )
    reasons, concerns = _outcome_reasons(
        readiness, reported_readiness, open_items, contradictions, hypotheses, findings
    )
    return (
        ReviewOutcomeVM(
            availability=availability,
            readiness=readiness,
            reported_readiness=reported_readiness,
            outcome_label=_OUTCOME_LABEL[readiness],
            outcome_statement=_OUTCOME_STATEMENT[readiness],
            reasons=reasons,
            recommendation=_build_recommendation(readiness, open_items, contradictions, hypotheses),
            blocking_count=sum(1 for i in open_items if i.is_blocking),
            advisory_count=sum(1 for i in open_items if not i.is_blocking),
        ),
        concerns,
    )


def _build_confidence_breakdown(
    overall: float | None,
    hypotheses: HypothesesSectionVM,
    concerns: list[str],
) -> ConfidenceBreakdownVM:
    """Requirement 1, implemented once, for every report: the strongest
    hypothesis's confidence and the investigation's overall confidence are
    two different measurements, labelled as such, and the gap between them
    is explained rather than left as two unrelated percentages.

    `divergence_note` is only emitted when the gap is real (at least
    `_CONFIDENCE_DIVERGENCE_THRESHOLD`) — a report where the two agree
    doesn't need a paragraph explaining that they agree.
    """
    top = hypotheses.items[0] if hypotheses.items else None
    top_confidence = top.entry.confidence if top else None
    # The basis names the leading *concern*, never a positive finding —
    # a low overall score explained by "2 findings were confirmed" reads
    # as a non-sequitur. Empty when there is nothing holding it back.
    basis_reason = concerns[0] if concerns else ""
    if overall is None:
        basis = (
            "No stage produced an overall confidence score, so how well the issue is "
            "understood has not been quantified."
        )
    else:
        basis = (
            f"{round(overall * 100)}% — confidence that the issue is understood well "
            "enough to implement a fix, across the whole investigation. " + basis_reason
        ).strip()

    divergence: str | None = None
    if (
        overall is not None
        and top_confidence is not None
        and abs(top_confidence - overall) >= _CONFIDENCE_DIVERGENCE_THRESHOLD
    ):
        divergence = (
            f"These two numbers measure different things. Root-cause candidate "
            f"confidence ({round(top_confidence * 100)}%) is confidence in one specific "
            f"hypothesis; overall resolution confidence ({round(overall * 100)}%) is "
            "confidence that the issue is understood and ready for implementation. "
            "A well-supported hypothesis inside a not-yet-ready investigation is a "
            "normal state, not a discrepancy."
        )

    return ConfidenceBreakdownVM(
        overall=overall,
        overall_label="Overall resolution confidence",
        overall_basis=basis,
        top_hypothesis_confidence=top_confidence,
        top_hypothesis_statement=top.entry.statement if top else None,
        top_hypothesis_label="Root-cause candidate confidence",
        divergence_note=divergence,
    )


def _build_confidence(
    bundles: dict[str, StageStepData | None],
    hypotheses: HypothesesSectionVM,
    concerns: list[str],
) -> ConfidenceSectionVM:
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
        breakdown=_build_confidence_breakdown(current, hypotheses, concerns),
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


def _build_findings(
    bundles: dict[str, StageStepData | None], ledger_rows: list[LedgerRow]
) -> FindingsSectionVM:
    """[ Confirmed Findings ] — the "what do we actually know" half of the
    knowledge split, as statements rather than counts.

    Two sources, both already deterministic: Context Discovery's own
    `verified` facts (`dp.map_confirmed_facts`) and every Knowledge Ledger
    row whose `verification_status` is VERIFIED (a real code check —
    Planning's `repository_usage[].verified`, and any hypothesis ADR 0025's
    correlation pass managed to verify). A supported hypothesis with no
    verification never reaches this list, at any confidence — that is
    exactly the confusion this section exists to prevent.
    """
    cd_bundle = bundles.get("context_discovery")
    items = list(dp.map_confirmed_facts(cd_bundle))
    for row in ledger_rows:
        if row.verification_status == VerificationStatus.VERIFIED:
            items.append(
                ConfirmedFinding(
                    statement=row.claim,
                    source_stage=row.source_stage,
                    source_field=row.source_field,
                    evidence_summary=None,
                )
            )
    if cd_bundle is None and not items:
        return FindingsSectionVM(
            availability=SectionAvailability(
                Availability.UNAVAILABLE,
                reason="Context Discovery did not complete for this workflow.",
            ),
            items=[],
            truncated_count=0,
        )
    availability = (
        SectionAvailability(Availability.AVAILABLE)
        if items
        else SectionAvailability(
            Availability.DEGRADED,
            reason=(
                "Nothing was independently verified in this investigation — every "
                "statement below it is reasoning, not confirmation."
            ),
        )
    )
    visible = items[:_MAX_CONFIRMED_FINDINGS]
    return FindingsSectionVM(
        availability=availability,
        items=visible,
        truncated_count=max(0, len(items) - len(visible)),
    )


def _build_contradictions(bundles: dict[str, StageStepData | None]) -> ContradictionsSectionVM:
    """Requirement 4: a contradiction is not just its statement and two
    evidence lists — the report also has to say what it does to the
    conclusion and what would settle it. Both are derived from the entry's
    own `resolved` flag and evidence counts; an unresolved contradiction is
    always blocking, which is what `build_open_items` then promotes into
    the one open-item list."""
    entries, availability, run_state = dp.map_contradictions(bundles.get("context_discovery"))
    items: list[ContradictionVM] = []
    for entry in entries:
        if entry.resolved:
            note = entry.resolution_note.strip()
            items.append(
                ContradictionVM(
                    entry=entry,
                    is_blocking=False,
                    impact=(
                        "Resolved — this no longer affects the conclusion."
                        + (f" {note}" if note else "")
                    ),
                    required_resolution="None; already settled.",
                )
            )
            continue
        items.append(
            ContradictionVM(
                entry=entry,
                is_blocking=True,
                impact=(
                    "Blocks the outcome: any conclusion that depends on this claim cannot "
                    "be relied on until the conflict is settled, so overall readiness is "
                    "reduced accordingly."
                ),
                required_resolution=(
                    "Establish which side is correct — "
                    f"{_count(len(entry.evidence_for), 'supporting item')} vs "
                    f"{_count(len(entry.evidence_against), 'conflicting item')} recorded. "
                    "Until then, treat both statements as unconfirmed."
                ),
            )
        )
    return ContradictionsSectionVM(
        availability=availability, synthesis_state=run_state, items=items
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


def _build_next_actions(open_items: list[OpenQuestionEntry]) -> NextActionsSectionVM:
    """Takes the already-built open-item list rather than re-deriving it —
    an empty "what's next" is a real, positive outcome (nothing blocking),
    and the counts here are the same ones the outcome section states."""
    return NextActionsSectionVM(
        availability=SectionAvailability(Availability.AVAILABLE),
        questions=list(open_items),
        blocking_count=sum(1 for i in open_items if i.is_blocking),
        advisory_count=sum(1 for i in open_items if not i.is_blocking),
    )


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
    # Build order matters: contradictions feed the open-item list, the
    # open-item list feeds readiness, readiness feeds the outcome, and the
    # outcome's reasons feed the confidence breakdown. Each value is
    # computed once and passed down — nothing below re-derives anything
    # above it, which is what keeps the sections consistent by
    # construction rather than by review.
    hypotheses = _build_hypotheses(bundles, ledger_rows)
    contradictions = _build_contradictions(bundles)
    findings = _build_findings(bundles, ledger_rows)
    open_items = build_open_items(bundles, contradictions.items)
    reported_readiness = dp.map_readiness(bundles.get("engineering_review"))
    readiness = _derive_readiness(reported_readiness, open_items)
    review_outcome, concerns = _build_review_outcome(
        bundles,
        readiness,
        reported_readiness,
        open_items,
        contradictions.items,
        hypotheses,
        findings,
    )
    return ReportViewModel(
        header=_build_header(workflow, bundles, readiness, reported_readiness),
        review_outcome=review_outcome,
        confidence=_build_confidence(bundles, hypotheses, concerns),
        timeline=_build_timeline(bundles),
        knowledge=_build_knowledge(bundles),
        findings=findings,
        hypotheses=hypotheses,
        contradictions=contradictions,
        evidence=_build_evidence(bundles),
        next_actions=_build_next_actions(open_items),
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
