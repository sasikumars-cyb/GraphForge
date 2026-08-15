"""Report V2 Phase 1 — data plumbing.

Pure, deterministic functions that fetch and normalize a workflow's
persisted stage data into the `contracts.py` vocabulary. No LLM calls
happen anywhere in this module — every function here is a lookup,
comparison, or straight passthrough of a named backend field. See each
function's docstring for its exact source field; that mapping is the
provenance contract Report V2's design requires (Revision 2.1, point 5).

What this module explicitly does NOT do (out of scope for Phase 1):
- Does not assemble the full nested `ReportViewModel` (HeaderVM, ScopeVM,
  ...) — that's Phase 2, the deterministic view-model builder.
- Does not render anything.
- Does not call an LLM.
- Does not reinterpret a value the source didn't state (see each
  function's docstring for the exact "if the source doesn't say X, this
  returns UNKNOWN/NOT_CHECKED/UNAVAILABLE, never a guess" behavior).

RESOLVED — an earlier revision of this module found that hypotheses/
contradictions (`engineering_understanding.hypotheses[]` in the original
Revision 2.1 contract) were not persisted anywhere `AgentStep.result`
reached. That has since been fixed upstream: `app.context_pipeline.
reasoning.projection.build_reasoning_summary` now projects the real
`Hypothesis`/`Contradiction` objects Context Discovery's *existing*
synthesis call already computes into `context_discovery.result
["reasoning_summary"]`, unconditionally. See `map_hypotheses`/
`map_contradictions`/`map_synthesis_status` below — no new LLM call was
added; this module still performs none.
"""

from __future__ import annotations

from typing import Any

from app.agents.git_ops._artifact_reader import HasRuns, StageStepData, get_stage_step_data
from app.agents.report_generation.contracts import (
    ArchitectureDiagramRef,
    Availability,
    ConfidenceJourney,
    ConfidenceStagePoint,
    ConfirmedFinding,
    ContradictionEntry,
    EvidenceCategoryCount,
    FileRole,
    HypothesisEntry,
    LedgerRow,
    OpenItemKind,
    OpenQuestionEntry,
    Readiness,
    RiskEntry,
    RiskSeverity,
    ScopeFileEntry,
    SectionAvailability,
    SubjectEntity,
    SubjectEntityKind,
    SynthesisRunState,
    SynthesisStatus,
    TimelineEntry,
    VerificationStatus,
)

# Same order/labels as report_generation/agent.py's _STAGE_ORDER — kept as
# an independent copy rather than importing that module's private
# constant, since Phase 1's stage list is a data-plumbing concern, not an
# artifact of how the (soon to be replaced) monolithic LLM report prompt
# happens to be built.
# The largest a fact *kind* can be and still be read as a set of findings
# rather than a coverage sweep — see `map_confirmed_facts` for the real
# report that produced this number (63 repositories and 12,130 components,
# every one "verified"). Deliberately small: a kind the investigation
# genuinely narrowed to a handful of items is a finding list; anything
# broader is inventory, and is reported as a count elsewhere.
_MAX_FINDING_GROUP_SIZE = 10

STAGE_ORDER: tuple[str, ...] = (
    "context_discovery",
    "planning",
    "development",
    "testing",
    "documentation_planning",
    "engineering_review",
)

STAGE_LABELS: dict[str, str] = {
    "context_discovery": "Context Discovery",
    "planning": "Planning",
    "development": "Development",
    "testing": "Testing",
    "documentation_planning": "Documentation Planning",
    "engineering_review": "Engineering Review",
}


# ---------------------------------------------------------------------------
# Raw fetch
# ---------------------------------------------------------------------------


def fetch_all_stage_bundles(workflow: HasRuns) -> dict[str, StageStepData | None]:
    """One `get_stage_step_data()` call per stage in `STAGE_ORDER`. `None`
    for a stage with no completed run — never substituted with an empty
    `StageStepData`, so "this stage didn't run" stays distinguishable from
    "this stage ran and produced nothing" at every call site downstream.
    """
    return {stage: get_stage_step_data(workflow, stage) for stage in STAGE_ORDER}


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def map_availability(
    stage: str,
    bundle: StageStepData | None,
    required_fields: tuple[str, ...] = (),
) -> SectionAvailability:
    """AVAILABLE when the stage ran and every field in `required_fields`
    is present (non-empty) on its result. DEGRADED when the stage ran but
    at least one required field is missing/empty. UNAVAILABLE when the
    stage never completed at all (`bundle is None`).

    `required_fields` lets a caller express "this section additionally
    needs X" (e.g. Architecture needs `blueprint`) without this function
    hard-coding per-section knowledge — see data_plumbing tests for the
    exact fixtures this decision tree is verified against.
    """
    if bundle is None:
        return SectionAvailability(
            Availability.UNAVAILABLE,
            reason=(
                f"The {STAGE_LABELS.get(stage, stage)} stage did not complete for this workflow."
            ),
        )
    missing = [f for f in required_fields if not bundle.result.get(f)]
    if missing:
        return SectionAvailability(
            Availability.DEGRADED,
            reason=(
                f"{STAGE_LABELS.get(stage, stage)} completed, but did not populate: "
                + ", ".join(missing)
            ),
        )
    return SectionAvailability(Availability.AVAILABLE)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def map_readiness(engineering_review_bundle: StageStepData | None) -> Readiness:
    """Source: Engineering Review's own `readiness_status` field
    (app.agents.engineering_review.schemas.EngineeringReadinessReport),
    already a deterministic, code-verified-and-LLM-set value (the
    Engineering Review agent's own "ready" downgrade override already
    ran by the time this is persisted — see app.agents.engineering_review.
    agent.py). This function performs a literal string match, nothing
    more; it does not re-derive readiness from any other field."""
    if engineering_review_bundle is None:
        return Readiness.UNKNOWN
    raw = str(engineering_review_bundle.result.get("readiness_status") or "").strip().lower()
    return {
        "ready": Readiness.READY,
        "needs_revision": Readiness.NEEDS_REVISION,
        "not_ready": Readiness.NOT_READY,
    }.get(raw, Readiness.UNKNOWN)


# ---------------------------------------------------------------------------
# Confidence journey
# ---------------------------------------------------------------------------


def map_confidence_journey(bundles: dict[str, StageStepData | None]) -> ConfidenceJourney:
    """Source: `AgentStep.confidence_score` per stage — the same
    already-computed weighted-evidence-engine score the live workflow UI
    renders as "CONFIDENCE BY STAGE". `delta_from_previous`/`dropped` are
    pure arithmetic over two real numbers, computed here, never estimated
    or phrased by an LLM. A stage with no completed run contributes a
    point with `confidence=None` (rendered as an unfilled point, per the
    Report V2 design) rather than being omitted from `points` — the gap
    itself is information.
    """
    points: list[ConfidenceStagePoint] = []
    previous: float | None = None
    drops: list[tuple[str, float, float]] = []
    for stage in STAGE_ORDER:
        bundle = bundles.get(stage)
        score = bundle.confidence_score if bundle else None
        delta = (score - previous) if (score is not None and previous is not None) else None
        dropped = delta is not None and delta < 0
        if dropped and previous is not None and score is not None:
            drops.append((STAGE_LABELS[stage], previous, score))
        points.append(
            ConfidenceStagePoint(
                stage=stage,
                label=STAGE_LABELS[stage],
                confidence=score,
                delta_from_previous=delta,
                dropped=dropped,
            )
        )
        if score is not None:
            previous = score

    if not drops:
        summary = "Confidence held steady or improved at every completed stage."
    else:
        parts = [
            f"{label} ({round(before * 100)}→{round(after * 100)})"
            for label, before, after in drops
        ]
        summary = "Dropped after " + " and ".join(parts) + "."
    return ConfidenceJourney(points=points, summary_sentence=summary)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def map_evidence_summary(evidence: list[dict[str, Any]]) -> list[EvidenceCategoryCount]:
    """Source: `AgentStep.evidence` (a stage's own `Evidence[]`, kind is
    one of the five literal values on app.agents._contract.Evidence.kind:
    graph_traversal/tool_call/graph_fact/llm_reasoning/human_input). Pure
    tally — never touches `summary` text, never loads more than counting
    requires (the large-repository safety rule: this function's output
    size is bounded by the number of distinct kinds, at most 5, regardless
    of how many evidence items a stage produced).
    """
    counts: dict[str, int] = {}
    for item in evidence:
        kind = str(item.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return [EvidenceCategoryCount(kind=k, count=v) for k, v in sorted(counts.items())]


# ---------------------------------------------------------------------------
# Investigation timeline
# ---------------------------------------------------------------------------


def map_investigation_timeline(
    context_discovery_bundle: StageStepData | None,
) -> tuple[list[TimelineEntry], SectionAvailability]:
    """Source: `context_discovery.result["discovery_report"]["investigation"]`
    — built by `app.context_pipeline.reasoning.projection.build_discovery_report`
    from `WorkingContext.ledger.evidence` (`EvidenceRecord`), which is
    real, persisted, structured data: `evidence_id`, `provider`, `action`,
    `outcome`, `summary`, `intent`, `iteration`. `summary`/`intent` are
    copied verbatim — they were authored once, at investigation time, by
    the reasoning engine itself; this function never re-generates or
    paraphrases them.

    UNAVAILABLE when Context Discovery didn't run, or ran but
    `discovery_report`/`investigation` is empty (a result persisted
    before `discovery_report` existed, or a run where investigation
    aborted before producing any evidence record) — never backfilled from
    a different field.
    """
    if context_discovery_bundle is None:
        return [], SectionAvailability(
            Availability.UNAVAILABLE, reason="Context Discovery did not complete for this workflow."
        )
    report = context_discovery_bundle.result.get("discovery_report") or {}
    raw_entries = report.get("investigation") or []
    if not raw_entries:
        return [], SectionAvailability(
            Availability.UNAVAILABLE,
            reason="Context Discovery completed but recorded no investigation trail "
            "(discovery_report.investigation is empty) — likely a result persisted "
            "before this field existed.",
        )
    entries = [
        TimelineEntry(
            cycle=int(e.get("iteration") or 0),
            provider=str(e.get("provider") or ""),
            action=str(e.get("action") or ""),
            outcome=str(e.get("outcome") or ""),
            summary=str(e.get("summary") or ""),
            intent=str(e.get("intent") or ""),
        )
        for e in raw_entries
    ]
    return entries, SectionAvailability(Availability.AVAILABLE)


# ---------------------------------------------------------------------------
# Knowledge ledger — verification axis only (see module docstring for why
# the synthesis axis is not populated in Phase 1)
# ---------------------------------------------------------------------------


def map_verification_status_from_repo_usage(usage: dict[str, Any]) -> VerificationStatus:
    """Source: Planning's `repository_usage[].verified` (bool, set by
    app.agents.planning.agent.py's deterministic
    `usage.verified = name_indexed and files_check.all_verified` — never
    LLM self-reported). True -> VERIFIED. False -> UNVERIFIED (a real
    check ran and did not pass) — never NOT_CHECKED, since the field's
    presence means the check happened."""
    return VerificationStatus.VERIFIED if usage.get("verified") else VerificationStatus.UNVERIFIED


def map_verification_status_from_finding(_finding: dict[str, Any]) -> VerificationStatus:
    """Source: any entry in a stage's `verification_findings[]`
    (app.agents.verification.VerificationFinding). By construction, an
    entry only exists when app.agents.verification's deterministic checks
    found a problem — there is no "positive" verification_findings entry
    — so every entry maps to UNVERIFIED. The parameter is accepted (not
    just a constant) so call sites read naturally per-row and so a future
    positive-finding shape doesn't silently get this wrong without a
    signature change forcing a look at this function."""
    return VerificationStatus.UNVERIFIED


def _parse_subject_entity(raw: dict[str, Any] | None) -> SubjectEntity | None:
    """Source: `Hypothesis.subject_entity` (app.context_pipeline.reasoning.
    understanding.HypothesisSubjectEntity) — ADR 0025 §7. `kind`/`name`
    must both be present and `kind` must be one of the three real values;
    anything else (missing field, unrecognized kind, malformed shape)
    parses as `None` — the same fail-safe-to-NOT_CHECKED posture as every
    other partial/malformed input in this module. Never guesses a kind
    from `name`'s shape."""
    if not raw:
        return None
    kind_raw = raw.get("kind")
    name_raw = raw.get("name")
    if not isinstance(kind_raw, str) or not isinstance(name_raw, str) or not name_raw:
        return None
    try:
        kind = SubjectEntityKind(kind_raw)
    except ValueError:
        return None
    return SubjectEntity(kind=kind, name=name_raw)


def map_verification_status_for_subject_entity(
    subject_entity: SubjectEntity | None,
    planning_bundle: StageStepData | None,
) -> VerificationStatus | None:
    """ADR 0025 §7/§8/§9a — the ONLY function permitted to turn a
    hypothesis's `verification_status` into anything other than `None`
    (rendered as `NOT_CHECKED`). Returns `None` unless ALL of §8's
    conditions hold; never a guess, never a default other than `None`.

    Scope of this implementation (a real constraint found while building
    this, not anticipated in the ADR): only `kind="repository"` is
    correlated. Planning's `repository_usage[]` is the only place in this
    codebase that persists a real, structured, per-item verified/
    unverified signal (`RepositoryUsage.verified: bool`) — traced across
    Development's `AffectedRepository` and Testing's `affected_
    repositories`, neither carries an equivalent per-file or per-
    component verified flag; only free-text `verification_findings[]`
    messages exist at that granularity, and matching against prose text
    is exactly the mechanism ADR 0025 §4 (Option B) rejects. A
    `kind="file"`/`"component"` `subject_entity` is schema-valid (a
    hypothesis may legitimately have one) but always resolves to `None`
    here today — the honest, correct answer given no real structured
    per-item signal exists to check it against, matrix row 10's own
    reasoning applied to a currently-missing data source rather than a
    currently-unavailable stage.

    Fail-closed on conflicting signals (§8 addendum, §9a row 9): if more
    than one `repository_usage[]` entry exactly matches `subject_entity.
    name`, any `verified=False` among them makes the result UNVERIFIED,
    even if another matching entry was `verified=True`.
    """
    if subject_entity is None:
        return None
    if subject_entity.kind != SubjectEntityKind.REPOSITORY:
        return None
    if planning_bundle is None:
        return None
    matches = [
        usage
        for usage in (planning_bundle.result.get("repository_usage") or [])
        if usage.get("name") == subject_entity.name
    ]
    if not matches:
        return None
    if any(not usage.get("verified") for usage in matches):
        return VerificationStatus.UNVERIFIED
    return VerificationStatus.VERIFIED


def map_knowledge_ledger_rows(
    planning_bundle: StageStepData | None,
    development_bundle: StageStepData | None = None,
    testing_bundle: StageStepData | None = None,
    context_discovery_bundle: StageStepData | None = None,
) -> list[LedgerRow]:
    """Builds the two-axis Knowledge Ledger (Report V2 design, point 2 —
    rows are never bucketed into 'confirmed'/'unresolved'; each keeps its
    own independent synthesis_status/verification_status pair, and most
    rows populate only one of the two axes).

    Sources, each verbatim from the named stage's persisted result:
    - Planning's `repository_usage[]` -> one row per entry,
      verification_status via `map_verification_status_from_repo_usage`,
      synthesis_status always `None` (a code check, not a hypothesis).
    - `verification_findings[]` on EACH of Planning, Development, and
      Testing (each stage runs its own independent checks — see
      app.agents.verification — so a finding that only Testing produced
      would otherwise be silently absent from the ledger). One row per
      entry, verification_status always UNVERIFIED, synthesis_status
      always `None`.
    - Context Discovery's `reasoning_summary.hypotheses[]` (via
      `map_hypotheses`) -> one row per hypothesis, synthesis_status via
      `map_synthesis_status`. `verification_status` is `None` for almost
      every hypothesis (a hypothesis is reasoning, never a code-run check
      — Phase 1's original design, unchanged) — ADR 0025 adds the one
      narrow exception: `map_verification_status_for_subject_entity`
      correlates a hypothesis to Planning's own `repository_usage[]`
      when, and only when, the hypothesis carries a claim-type-gated,
      exact-match `subject_entity` (see that function's own docstring for
      the precise, tested condition — never a scope-only or textual
      match).
    """
    rows: list[LedgerRow] = []
    hypotheses, _, _ = map_hypotheses(context_discovery_bundle)
    for i, hypothesis in enumerate(hypotheses):
        rows.append(
            LedgerRow(
                claim=hypothesis.statement[:160],
                source_stage="context_discovery",
                source_field=f"reasoning_summary.hypotheses[{i}]",
                synthesis_status=hypothesis.status,
                verification_status=map_verification_status_for_subject_entity(
                    hypothesis.subject_entity, planning_bundle
                ),
            )
        )
    if planning_bundle is not None:
        for i, usage in enumerate(planning_bundle.result.get("repository_usage") or []):
            rows.append(
                LedgerRow(
                    claim=f"Repository usage: {usage.get('name', '?')}",
                    source_stage="planning",
                    source_field=f"repository_usage[{i}]",
                    synthesis_status=None,
                    verification_status=map_verification_status_from_repo_usage(usage),
                )
            )
    for stage_name, bundle in (
        ("planning", planning_bundle),
        ("development", development_bundle),
        ("testing", testing_bundle),
    ):
        if bundle is None:
            continue
        for i, finding in enumerate(bundle.result.get("verification_findings") or []):
            message = str(finding.get("message") or "")
            rows.append(
                LedgerRow(
                    claim=message[:160],
                    source_stage=stage_name,
                    source_field=f"verification_findings[{i}]",
                    synthesis_status=None,
                    verification_status=map_verification_status_from_finding(finding),
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Hypotheses / Contradictions
#
# Upstream correction (post-Phase-1): Context Discovery's synthesis call
# already computes real Hypothesis/Contradiction objects at runtime (see
# app.context_pipeline.reasoning.understanding.synthesize_engineering_
# understanding) — they simply weren't persisted anywhere AgentStep.result
# reached. app.context_pipeline.reasoning.projection.build_reasoning_
# summary now projects exactly those two lists (and nothing else from the
# workspace) into `context_discovery.result["reasoning_summary"]`,
# unconditionally (not gated on whether the run paused for clarification).
# No new LLM call was added — this is purely a persistence/projection fix
# at the boundary where the data was already being computed and thrown
# away. The functions below read that field; they do not compute anything
# a synthesis pass didn't already decide.
# ---------------------------------------------------------------------------


def map_synthesis_status(raw_hypothesis_status: str) -> SynthesisStatus:
    """Source: `Hypothesis.status`
    (app.context_pipeline.reasoning.understanding.HypothesisStatus =
    Literal["supported", "rejected", "unknown"]) — a real, already-set
    classification from the synthesis LLM call, not derived here.

    Deliberately a straight 3-way mapping, not 4-way: `HypothesisStatus`
    has no state corresponding to `SynthesisStatus.INFERRED` (partial
    support with no accompanying confidence-based synonym for it in the
    source enum), and manufacturing one from a confidence threshold would
    be exactly the kind of "faking the synthesis status from arbitrary
    text/numbers" the Report V2 design forbids. `INFERRED` remains a
    valid `SynthesisStatus` value for any future source that actually
    distinguishes it; this function simply never produces it.
    """
    return {
        "supported": SynthesisStatus.SUPPORTED,
        "rejected": SynthesisStatus.CONTRADICTED,
        "unknown": SynthesisStatus.UNKNOWN,
    }.get(raw_hypothesis_status, SynthesisStatus.UNKNOWN)


def map_synthesis_run_state(
    context_discovery_bundle: StageStepData | None,
) -> SynthesisRunState:
    """Source: `context_discovery.result["reasoning_summary"]["synthesis_state"]`
    (app.context_pipeline.reasoning.projection._resolve_synthesis_run_state)
    — a real, already-computed classification of whether reasoning
    *execution itself* succeeded, distinct from any per-hypothesis belief
    (see `map_synthesis_status`). ADR 0024 §11 is the source of truth for
    what each value means.

    `NOT_RUN` whenever the raw string is missing entirely — no Context
    Discovery bundle, a `reasoning_summary` predating this field, or an
    unrecognized value — never a guess at `COMPLETED`. This is the one
    function every degraded-state UI decision should read; `map_hypotheses`/
    `map_contradictions` call it internally rather than re-deriving it.
    """
    if context_discovery_bundle is None:
        return SynthesisRunState.NOT_RUN
    raw = (context_discovery_bundle.result.get("reasoning_summary") or {}).get("synthesis_state")
    if not isinstance(raw, str):
        return SynthesisRunState.NOT_RUN
    try:
        return SynthesisRunState(raw)
    except ValueError:
        return SynthesisRunState.NOT_RUN


_RUN_STATE_UNAVAILABLE_REASON: dict[SynthesisRunState, str] = {
    SynthesisRunState.NOT_RUN: ("Reasoning synthesis was not recorded for this investigation."),
    SynthesisRunState.FAILED: (
        "Reasoning synthesis failed for this investigation — falling back to "
        "evidence-only findings. This is not the same as 'no hypotheses found.'"
    ),
}


def map_hypotheses(
    context_discovery_bundle: StageStepData | None,
) -> tuple[list[HypothesisEntry], SectionAvailability, SynthesisRunState]:
    """Source: `context_discovery.result["reasoning_summary"]["hypotheses"]`
    (app.context_pipeline.reasoning.projection.build_reasoning_summary) —
    each entry a straight passthrough of the real `Hypothesis` model's
    fields (`description`->`statement`, `status`, `confidence`,
    `supporting_evidence`, `contradicting_evidence`). `supporting_
    evidence`/`contradicting_evidence` are copied as the prose strings
    they are in the source — never treated as Evidence IDs, never used to
    draw a claimed graph edge to a specific evidence item (no stable ID
    exists linking them; see the source model's own fields).

    Availability now follows `SynthesisRunState` (ADR 0024 §11), not a bare
    "is the list empty" check — `COMPLETED_EMPTY` (synthesis genuinely ran
    and found nothing) is `AVAILABLE` with an empty list, never the same
    `UNAVAILABLE` result as `NOT_RUN`/`FAILED` used to collapse it into.
    Also returns the raw `SynthesisRunState` itself so a caller can render
    the exact one of the four state-specific copy variants (§11) rather
    than reverse-engineering it from availability + list length.
    """
    run_state = map_synthesis_run_state(context_discovery_bundle)
    if run_state in _RUN_STATE_UNAVAILABLE_REASON:
        return (
            [],
            SectionAvailability(
                (
                    Availability.UNAVAILABLE
                    if run_state == SynthesisRunState.NOT_RUN
                    else Availability.DEGRADED
                ),
                reason=_RUN_STATE_UNAVAILABLE_REASON[run_state],
            ),
            run_state,
        )
    assert context_discovery_bundle is not None  # run_state above rules out NOT_RUN otherwise
    raw = (context_discovery_bundle.result.get("reasoning_summary") or {}).get("hypotheses") or []
    entries = [
        HypothesisEntry(
            statement=str(h.get("description") or ""),
            status=map_synthesis_status(str(h.get("status") or "unknown")),
            confidence=float(h.get("confidence") or 0.0),
            supporting_evidence=[str(x) for x in h.get("supporting_evidence") or []],
            contradicting_evidence=[str(x) for x in h.get("contradicting_evidence") or []],
            subject_entity=_parse_subject_entity(h.get("subject_entity")),
        )
        for h in raw
    ]
    return entries, SectionAvailability(Availability.AVAILABLE), run_state


def map_contradictions(
    context_discovery_bundle: StageStepData | None,
) -> tuple[list[ContradictionEntry], SectionAvailability, SynthesisRunState]:
    """Source: `context_discovery.result["reasoning_summary"]["contradictions"]`
    — same provenance, prose-not-IDs caveat, and `SynthesisRunState`-driven
    availability as `map_hypotheses`. Note `discovery_report.gaps[]`
    entries with `status == "refuted"` are a related but distinct signal
    (a human claim investigated and not corroborated) — deliberately not
    merged in here; a Contradiction specifically carries an
    evidence_for/evidence_against pair a refuted gap does not.
    """
    run_state = map_synthesis_run_state(context_discovery_bundle)
    if run_state in _RUN_STATE_UNAVAILABLE_REASON:
        return (
            [],
            SectionAvailability(
                (
                    Availability.UNAVAILABLE
                    if run_state == SynthesisRunState.NOT_RUN
                    else Availability.DEGRADED
                ),
                reason=_RUN_STATE_UNAVAILABLE_REASON[run_state],
            ),
            run_state,
        )
    assert context_discovery_bundle is not None
    raw = (context_discovery_bundle.result.get("reasoning_summary") or {}).get(
        "contradictions"
    ) or []
    entries = [
        ContradictionEntry(
            statement=str(c.get("description") or ""),
            evidence_for=[str(x) for x in c.get("evidence_for") or []],
            evidence_against=[str(x) for x in c.get("evidence_against") or []],
            resolved=bool(c.get("resolved")),
            resolution_note=str(c.get("resolution_note") or ""),
        )
        for c in raw
    ]
    return entries, SectionAvailability(Availability.AVAILABLE), run_state


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def map_file_role(component: dict[str, Any], repository_usage: list[dict[str, Any]]) -> FileRole:
    """Source: Development's `components[]` entries
    (app.agents.development.schemas.AffectedComponent) default to
    MODIFIED — that schema's own docstring defines the type as "A
    service/component that requires modification", so absent any other
    signal, MODIFIED is what the field means, not a guess.

    The one override this function makes: if the same file_path appears
    in Planning's `repository_usage[].files_affected` for a usage entry
    where `verified == False`, the file is PROPOSED_UNVERIFIED instead —
    this is a real, deterministic cross-reference (Planning's own
    verification result), not an inference.

    NOT implemented (see Phase 1 audit): CONSULTED vs DEPENDENCY.
    `AffectedComponent` has no structured field distinguishing these from
    MODIFIED — only a free-text `change_description` that sometimes
    reads "No change; ..." in practice. Pattern-matching that string
    would be exactly the kind of reinterpretation Phase 1 must not do;
    every component from Development is reported as MODIFIED until a
    structured field exists to say otherwise.
    """
    file_path = component.get("file_path", "")
    for usage in repository_usage:
        if not usage.get("verified") and file_path in (usage.get("files_affected") or []):
            return FileRole.PROPOSED_UNVERIFIED
    return FileRole.MODIFIED


def map_scope(
    development_bundle: StageStepData | None,
    planning_bundle: StageStepData | None,
) -> tuple[list[ScopeFileEntry], str | None]:
    """Prefers Development's `components[]` (repository/file_path/
    change_description, each cross-referenced against Planning's
    `repository_usage[].verified` via `map_file_role`). Falls back to
    Planning's `repositories_consulted`-scoped `repository_usage[].
    files_affected` (all reported PROPOSED_UNVERIFIED or MODIFIED
    per that same usage entry's `verified` flag) only when Development
    never ran — the fallback source is returned as the second tuple
    element so a caller can render "unconfirmed — Development did not
    run" per the Report V2 design's explicit requirement for this case.
    """
    planning_usage: list[dict[str, Any]] = (
        (planning_bundle.result.get("repository_usage") or []) if planning_bundle else []
    )

    if development_bundle is not None:
        entries = [
            ScopeFileEntry(
                path=c.get("file_path", ""),
                repository=c.get("repository", ""),
                role=map_file_role(c, planning_usage),
                description=c.get("change_description") or None,
            )
            for c in (development_bundle.result.get("components") or [])
            if c.get("file_path")
        ]
        return entries, "development"

    entries = []
    for usage in planning_usage:
        role = FileRole.PROPOSED_UNVERIFIED if not usage.get("verified") else FileRole.MODIFIED
        for path in usage.get("files_affected") or []:
            entries.append(
                ScopeFileEntry(
                    path=path,
                    repository=usage.get("name", ""),
                    role=role,
                    description=None,
                )
            )
    return entries, ("planning_fallback" if entries else None)


# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------


def map_architecture_diagrams(
    bundle: StageStepData | None, source_stage: str
) -> list[ArchitectureDiagramRef]:
    """Source: `<stage>.result["blueprint"]["diagrams"][]`, each carrying
    its own `metadata.grounded` bool (set at blueprint-generation time —
    see planning/development agent blueprint builders). `grounded` is
    passed through verbatim; this function never computes or infers it.
    A diagram with no `metadata.grounded` key at all (a blueprint
    persisted before that field existed) is treated as `grounded=False`
    — the conservative default per Report V2 design point 7 ("never let
    a conceptual diagram look equally authoritative"): the absence of an
    explicit groundedness claim is not evidence of groundedness.
    """
    if bundle is None:
        return []
    blueprint = bundle.result.get("blueprint") or {}
    diagrams = blueprint.get("diagrams") or []
    refs: list[ArchitectureDiagramRef] = []
    for d in diagrams:
        grounded = bool((d.get("metadata") or {}).get("grounded", False))
        refs.append(
            ArchitectureDiagramRef(
                diagram_id=str(d.get("id") or ""),
                title=str(d.get("title") or ""),
                grounded=grounded,
                grounded_label="Graph-grounded" if grounded else "Conceptual — not graph-derived",
                source_stage=source_stage,
            )
        )
    return refs


# ---------------------------------------------------------------------------
# Risks
# ---------------------------------------------------------------------------


def _map_severity(raw: str | None) -> RiskSeverity:
    if not raw:
        return RiskSeverity.UNSPECIFIED
    try:
        return RiskSeverity(raw.strip().lower())
    except ValueError:
        return RiskSeverity.UNSPECIFIED


def map_risks(
    development_bundle: StageStepData | None,
    engineering_review_bundle: StageStepData | None,
) -> list[RiskEntry]:
    """Two independent sources, kept as separate entries rather than
    fuzzy-merged by description text (matching risk prose across stages
    is exactly the kind of reinterpretation Phase 1 must not do — Phase 2
    can decide whether/how to correlate them once the contract says how):

    - Development's `risks[]` (description/severity/mitigation) ->
      `mitigated=None` always (Development doesn't judge its own risks'
      mitigation adequacy; Engineering Review does that, separately).
    - Engineering Review's `risk_assessment[]` (description/
      adequately_mitigated/concern) -> `mitigated` is that stage's own
      real bool, never defaulted to True/False when absent.
    """
    entries: list[RiskEntry] = []
    if development_bundle is not None:
        for r in development_bundle.result.get("risks") or []:
            entries.append(
                RiskEntry(
                    description=str(r.get("description") or ""),
                    severity=_map_severity(r.get("severity")),
                    mitigated=None,
                    mitigation_text=r.get("mitigation") or None,
                    source_stage="development",
                )
            )
    if engineering_review_bundle is not None:
        for r in engineering_review_bundle.result.get("risk_assessment") or []:
            entries.append(
                RiskEntry(
                    description=str(r.get("description") or ""),
                    severity=RiskSeverity.UNSPECIFIED,
                    mitigated=(
                        bool(r.get("adequately_mitigated")) if "adequately_mitigated" in r else None
                    ),
                    mitigation_text=r.get("concern") or None,
                    source_stage="engineering_review",
                )
            )

    def _sort_key(entry: RiskEntry) -> tuple[int, int]:
        severity_rank = {
            RiskSeverity.CRITICAL: 0,
            RiskSeverity.HIGH: 1,
            RiskSeverity.MEDIUM: 2,
            RiskSeverity.LOW: 3,
            RiskSeverity.UNSPECIFIED: 4,
        }[entry.severity]
        unmitigated_first = 0 if entry.mitigated is False else (1 if entry.mitigated else 2)
        return (unmitigated_first, severity_rank)

    return sorted(entries, key=_sort_key)


# ---------------------------------------------------------------------------
# Open questions
# ---------------------------------------------------------------------------


def map_open_questions(
    context_discovery_bundle: StageStepData | None,
    engineering_review_bundle: StageStepData | None,
) -> list[OpenQuestionEntry]:
    """Sources:
    - Engineering Review's `blocking_issues[]` -> `is_blocking=True`.
    - Context Discovery's `discovery_report.gaps[]` where
      `status in ("open", "claimed", "unresolvable")` (real, stable
      `GapStatus` values — see app.context_pipeline.reasoning.memory.
      KnowledgeGap) -> `is_blocking = severity == "blocking"` (the gap's
      own real severity field, not re-derived).

    `gaps[].status == "refuted"` is deliberately excluded here — a
    refuted gap is a resolved (if negative) answer, not an open question;
    conflating the two would misrepresent it as still-unresolved.

    This function covers only the two *stage-result* sources above. The
    third real source of an open item — an unresolved contradiction — is
    reasoning output, not a stage field, and is merged in one level up by
    `view_model.build_open_items`, which is the single list every section
    counts from.
    """
    entries: list[OpenQuestionEntry] = []
    if engineering_review_bundle is not None:
        for text in engineering_review_bundle.result.get("blocking_issues") or []:
            entries.append(
                OpenQuestionEntry(
                    text=str(text),
                    source_stage="engineering_review",
                    is_blocking=True,
                    kind=OpenItemKind.BLOCKING_ISSUE,
                )
            )
    if context_discovery_bundle is not None:
        report = context_discovery_bundle.result.get("discovery_report") or {}
        for gap in report.get("gaps") or []:
            if gap.get("status") in ("open", "claimed", "unresolvable"):
                entries.append(
                    OpenQuestionEntry(
                        text=str(gap.get("summary") or gap.get("gap_id") or ""),
                        source_stage="context_discovery",
                        is_blocking=gap.get("severity") == "blocking",
                        kind=OpenItemKind.KNOWLEDGE_GAP,
                    )
                )
    return entries


def map_confirmed_facts(
    context_discovery_bundle: StageStepData | None,
) -> list[ConfirmedFinding]:
    """Source: `context_discovery.result["discovery_report"]["findings"][]
    ["items"][]` where the item's own `verified` flag is True (set by the
    reasoning ledger's `Fact.verified` — a real per-fact signal, see
    app.context_pipeline.reasoning.projection._findings). Only verified
    facts are returned: an unverified fact is context, not a confirmed
    finding, and is never upgraded here by counting, confidence, or the
    presence of a hypothesis that happens to mention it.

    `statement` is the fact's own `subject` string, copied verbatim;
    `evidence_summary` is the evidence record that established it (already
    attached by the same projection), also verbatim. Nothing is
    paraphrased — this module never re-words a source.

    A whole fact *kind* is skipped when it holds more than
    `_MAX_FINDING_GROUP_SIZE` facts. Found by rendering a real report: a
    single investigation's ledger legitimately carries 63 `repository` and
    12,130 `component` facts — the retrieval's coverage, every one of them
    "verified", none of them a finding about the problem. Listing those
    turned Confirmed Findings into the retrieval log this document format
    exists to get away from, and buried the two facts that mattered under
    118 that didn't. Coverage at that scale is already reported, correctly,
    as a per-kind count in the Coverage panel (`view_model._build_
    knowledge`); this section lists only kinds the investigation actually
    narrowed to a specific, readable set. The rule is a property of the
    data's shape, never of any particular kind name — a run that genuinely
    narrows to three repositories lists those three."""
    if context_discovery_bundle is None:
        return []
    report = context_discovery_bundle.result.get("discovery_report") or {}
    findings: list[ConfirmedFinding] = []
    # Several facts of one kind routinely share a subject — six
    # `repository_relationship` facts all name the same target repository,
    # for instance (seen in a real report: the same line five times over,
    # pushing the findings that mattered past the display cap). The same
    # statement repeated is not additional knowledge, so the first
    # occurrence is kept and later identical ones are dropped. Only exact
    # statement equality dedupes — never a fuzzy or partial text match.
    seen: set[str] = set()
    for group in report.get("findings") or []:
        kind = str(group.get("kind") or "finding")
        # `total` is the fact ledger's own true count for this kind, not
        # `len(items)` — the upstream projection already caps the listed
        # items at 50 per kind (`_MAX_REPORTED_FACTS_PER_KIND`), so reading
        # the list length here would make a 12,000-fact sweep look like 50
        # hand-picked findings. Always compare against `total`.
        total = int(group.get("total") or len(group.get("items") or []))
        if total > _MAX_FINDING_GROUP_SIZE:
            continue
        for i, item in enumerate(group.get("items") or []):
            if not item.get("verified"):
                continue
            subject = str(item.get("subject") or "").strip()
            if not subject:
                continue
            statement = f"{kind.replace('_', ' ')}: {subject}"
            if statement in seen:
                continue
            seen.add(statement)
            evidence = item.get("evidence") or {}
            findings.append(
                ConfirmedFinding(
                    statement=statement,
                    source_stage="context_discovery",
                    source_field=f"discovery_report.findings[{kind}].items[{i}]",
                    evidence_summary=str(evidence.get("summary") or "") or None,
                )
            )
    return findings
