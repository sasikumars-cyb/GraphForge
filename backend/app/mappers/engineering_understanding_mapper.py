"""Engineering Understanding mapper — pure, deterministic projection.

Transforms a typed ``ProjectionInput`` into an ``EngineeringUnderstandingDTO``
for the Context Explorer.  This module contains **no I/O, no DB access, no LLM
calls, no side-effects, and no parsing**.  The caller (API endpoint) is
responsible for parsing persisted data into the ``ProjectionInput`` shape.

Determinism guarantee
~~~~~~~~~~~~~~~~~~~~~
For identical ``ProjectionInput`` values, ``map_to_dto()`` **always** produces
identical ``EngineeringUnderstandingDTO`` output.  The mapper never depends on
current time, randomness, environment variables, external services, or mutable
global state.

Mapping validation table
~~~~~~~~~~~~~~~~~~~~~~~~

- business_goal ← .business_objective (fallback original_request)
- current_situation ← .current_behavior
- expected_outcome ← .desired_behavior
- repository_summary ← .primary_repository + supporting + ownership
- architecture_summary ← .architecture_relationships (join)
- relevant_areas ← EvidencePackage, grouped by tier (Production Code / Architecture /
  Reusable Components / Tests)
- files_to_review ← EvidencePackage's must_modify tier (falls back to architecture_dependency),
  ranked file paths
- known_constraints ← .constraints (list copy)
- missing_information ← gap_summaries + remaining_unknowns (dedup)
- unknowns ← remaining + rejected + unavailable (categorise)
- evidence_summary ← EvidencePackage.items by tier (summarise)
- recommendations ← insights + risks (prefix "Risk: ")
- planning_assessment ← readiness + factors + blocking (checklist)
- confidence_explanation ← factors (completed/outstanding prose)
- documentation_status ← pass-through (caller-derived)
- next_step ← pass-through (caller-derived)
- completion_status ← pass-through (caller-derived, see WorkingContext.completion_status)
- reasoning_summary ← workspace + investigation_priority (hypotheses/contradictions/next-up)
- debug_bundle ← pass-through (None when include_debug=False)
"""

from __future__ import annotations

from typing import get_args

from app.context_pipeline.reasoning import capabilities as capability_registry
from app.context_pipeline.reasoning.curation import EvidencePackage, Tier
from app.context_pipeline.reasoning.understanding import (
    Contradiction,
    Hypothesis,
    InvestigationWorkspace,
)
from app.schemas.engineering_understanding import (
    AreaClusterDTO,
    CapabilityFactor,
    ContradictionDTO,
    EngineeringUnderstandingDTO,
    HypothesisDTO,
    NextInvestigationDTO,
    PlanningAssessmentDTO,
    PlanningFactorDTO,
    ProjectionInput,
    Readiness,
    ReasoningSummaryDTO,
    RepositorySummaryDTO,
    UnknownItemDTO,
)

# Derive tier values and labels from the domain Tier type at import time.
# This avoids duplicating the tier list — if the domain adds a new tier,
# only a label entry needs updating here.
_TIER_VALUES: tuple[str, ...] = get_args(Tier)
_TIER_LABELS: dict[str, str] = {
    "must_modify": "Must-modify",
    "architecture_dependency": "Architecture dependency",
    "reusable_component": "Reusable component",
    "relevant_test": "Relevant test",
}

# `relevant_areas`'s own labels — deliberately separate from `_TIER_LABELS`
# above (a different reader: a fast-scanning "where do I look" grouping,
# not the "Evidence Summary" line's compact tally) and deliberately named
# after what each tier's own scoring actually measures, never a category
# the data can't support. `reusable_component` fires on util/helper/base-
# shaped *names within the same codebase* (see `curation._REUSE_NAME_RE`)
# — real dependencies from *other* repositories aren't a tier this system
# computes at all, so this is labelled for what it is, not relabelled to
# imply a category ("External Dependencies") this data was never scored
# against.
_AREA_TIER_LABELS: dict[str, str] = {
    "must_modify": "Production Code",
    "architecture_dependency": "Architecture",
    "reusable_component": "Reusable Components",
    "relevant_test": "Tests",
}
# How many items one cluster shows before collapsing into "and N more" —
# the actual fix for the wall-of-hundreds-of-test-names finding: every
# tier is capped here, same "ranked, capped, honest total" contract
# `_map_evidence_summary` already uses.
_MAX_AREA_ITEMS = 12


def map_to_dto(
    projection_input: ProjectionInput,
    *,
    include_debug: bool = False,
) -> EngineeringUnderstandingDTO:
    """Map a typed ``ProjectionInput`` to an ``EngineeringUnderstandingDTO``.

    This function is **pure and deterministic**: identical input always
    produces identical output.
    """
    u = projection_input.understanding
    p = projection_input

    # --- Inlined trivial mappings ---

    business_goal = u.business_objective or p.original_request
    current_situation = u.current_behavior
    expected_outcome = u.desired_behavior

    repository_summary = RepositorySummaryDTO(
        primary=u.primary_repository,
        supporting=list(u.supporting_repositories),
        ownership=list(u.implementation_ownership),
    )

    architecture_summary = "\n".join(u.architecture_relationships)

    known_constraints = list(u.constraints)

    # missing_information: gap_summaries + remaining_unknowns, deduplicated
    seen: set[str] = set()
    missing_information: list[str] = []
    for item in [*p.gap_summaries, *u.remaining_unknowns]:
        if item not in seen:
            seen.add(item)
            missing_information.append(item)

    # recommendations: insights + risks (prefixed)
    recommendations = list(u.engineering_insights) + [
        f"Risk: {r}" for r in u.risks
    ]

    # confidence_explanation: partition by satisfied
    completed = [f.label for f in p.capability_factors if f.satisfied]
    outstanding = [
        f.label for f in p.capability_factors if not f.satisfied
    ]
    parts: list[str] = []
    if completed:
        parts.append(f"Completed: {', '.join(completed)}.")
    if outstanding:
        parts.append(f"Outstanding: {', '.join(outstanding)}.")
    confidence_explanation = " ".join(parts)

    # --- Extracted helpers ---

    relevant_areas = _map_areas(p.evidence_package)
    files_to_review = _map_files_to_review(p.evidence_package)

    unknowns = _map_unknowns(
        u.remaining_unknowns,
        u.rejected_assumptions,
        p.unavailable_gaps,
    )

    evidence_summary = _map_evidence_summary(p.evidence_package)

    planning_assessment = _map_planning(
        p.readiness,
        p.capability_factors,
        p.blocking_reasons,
    )

    reasoning_summary = _map_reasoning(p.workspace, p.investigation_priority)

    return EngineeringUnderstandingDTO(
        business_goal=business_goal,
        current_situation=current_situation,
        expected_outcome=expected_outcome,
        repository_summary=repository_summary,
        architecture_summary=architecture_summary,
        relevant_areas=relevant_areas,
        files_to_review=files_to_review,
        known_constraints=known_constraints,
        missing_information=missing_information,
        unknowns=unknowns,
        evidence_summary=evidence_summary,
        recommendations=recommendations,
        planning_assessment=planning_assessment,
        confidence_explanation=confidence_explanation,
        documentation_status=p.documentation_status,
        next_step=p.next_step,
        completion_status=p.completion_status,
        reasoning_summary=reasoning_summary,
        debug_bundle=p.debug_bundle if include_debug else None,
    )


# ---------------------------------------------------------------------------
# Extracted helpers (non-trivial)
# ---------------------------------------------------------------------------


def _map_areas(evidence: EvidencePackage) -> list[AreaClusterDTO]:
    """Group the curated `EvidencePackage` by tier — Production Code /
    Architecture / Reusable Components / Tests, in that fixed order — each
    ranked (already sorted by `curate()`) and capped at `_MAX_AREA_ITEMS`,
    with the real total alongside so a large cluster reads as "12 of 340
    shown," never as a silent partial list or a wall of everything.

    Deliberately no longer grouped by graph *topic* against the raw,
    unranked component list — that grouping had no ranking at all and
    could (and, on a real investigation, did) render as several hundred
    ungrouped test-function names with the one file that actually
    mattered not even guaranteed to be among them. A tier with zero items
    is omitted outright rather than shown empty — an empty "Tests" bucket
    tells an engineer nothing an absent one doesn't already say clearer.
    """
    clusters: list[AreaClusterDTO] = []
    for tier in _TIER_VALUES:
        items = evidence.by_tier(tier)  # type: ignore[arg-type]
        if not items:
            continue
        clusters.append(
            AreaClusterDTO(
                name=_AREA_TIER_LABELS.get(tier, tier),
                components=[item.name for item in items[:_MAX_AREA_ITEMS]],
                total=len(items),
            )
        )
    return clusters


_MAX_FILES_TO_REVIEW = 8
_FILES_TO_REVIEW_TIERS: tuple[str, ...] = ("must_modify", "architecture_dependency")


def _map_files_to_review(evidence: EvidencePackage) -> list[str]:
    """Deduped, ranked file paths an engineer should open first.

    Reads only `must_modify` — the tier `curate()` already reserves for
    "this is what the request is actually about," relevance/proximity/
    ownership-scored and test-penalised — falling back to
    `architecture_dependency` only to fill the cap when `must_modify`
    alone has fewer than `_MAX_FILES_TO_REVIEW` real paths. Deliberately
    never reads `relevant_test`/`reusable_component`: a test file
    legitimately belongs under "Tests" in `_map_areas`, not in "open
    these first to understand the bug" — conflating the two is the exact
    finding this function exists to fix (a real investigation's "Files to
    Review" once listed eight test files and omitted the one production
    file its own Engineering Summary had just named as the root cause).
    """
    paths: list[str] = []
    for tier in _FILES_TO_REVIEW_TIERS:
        for item in evidence.by_tier(tier):  # type: ignore[arg-type]
            if not item.path or item.path in paths:
                continue
            paths.append(item.path)
            if len(paths) >= _MAX_FILES_TO_REVIEW:
                return paths
    return paths


def _map_unknowns(
    remaining: list[str],
    rejected: list[str],
    unavailable: list[str],
) -> list[UnknownItemDTO]:
    """Categorise unknowns into three buckets."""
    items: list[UnknownItemDTO] = []
    for desc in remaining:
        items.append(UnknownItemDTO(category="unknown", description=desc))
    for desc in rejected:
        items.append(UnknownItemDTO(category="known", description=desc))
    for desc in unavailable:
        items.append(
            UnknownItemDTO(category="unavailable", description=desc),
        )
    return items


def _map_evidence_summary(evidence: EvidencePackage) -> list[str]:
    """Summarise evidence per tier with ≤3 example names and counts.

    Iterates over the domain's ``Tier`` values (derived at import time)
    to avoid duplicating the tier list.
    """
    lines: list[str] = []
    for tier in _TIER_VALUES:
        items = evidence.by_tier(tier)  # type: ignore[arg-type]
        if not items:
            continue
        label = _TIER_LABELS.get(tier, tier)
        examples = [item.name for item in items[:3]]
        example_str = ", ".join(examples)
        if len(items) > 3:
            lines.append(
                f"{label} ({len(items)}): {example_str}, "
                f"and {len(items) - 3} more"
            )
        else:
            lines.append(f"{label} ({len(items)}): {example_str}")

    if evidence.excluded_count > 0:
        lines.append(
            f"{evidence.excluded_count} additional components evaluated "
            f"but excluded from evidence package."
        )

    return lines


def _map_hypothesis(index: int, hypothesis: Hypothesis, *, is_strongest: bool) -> HypothesisDTO:
    return HypothesisDTO(
        id=f"hyp_{index}",
        description=hypothesis.description,
        status=hypothesis.status,
        confidence=hypothesis.confidence,
        supporting_evidence=list(hypothesis.supporting_evidence),
        contradicting_evidence=list(hypothesis.contradicting_evidence),
        is_strongest=is_strongest,
    )


def _map_contradiction(index: int, contradiction: Contradiction) -> ContradictionDTO:
    return ContradictionDTO(
        id=f"contra_{index}",
        description=contradiction.description,
        evidence_for=list(contradiction.evidence_for),
        evidence_against=list(contradiction.evidence_against),
        resolved=contradiction.resolved,
        resolution_note=contradiction.resolution_note,
    )


def _strongest_hypothesis_index(hypotheses: list[Hypothesis]) -> int | None:
    """The single highest-confidence non-rejected hypothesis, or `None` when
    every hypothesis was rejected (or there are none) — never a bare
    argmax over the raw list, which would happily crown a rejected
    hypothesis "strongest" just because it once scored a high confidence
    before being eliminated."""
    best_index: int | None = None
    best_confidence = -1.0
    for index, hypothesis in enumerate(hypotheses):
        if hypothesis.status == "rejected":
            continue
        if hypothesis.confidence > best_confidence:
            best_confidence = hypothesis.confidence
            best_index = index
    return best_index


def _map_next_investigation(priority: dict[str, float]) -> list[NextInvestigationDTO]:
    """Ranked, highest priority first. Skips any label `capability_priority`
    might have emitted that isn't a real, registered capability — the same
    "an unlisted capability cannot be acted on" discipline `reasoning.
    understanding._KNOWN_CAPABILITIES` already applies before this dict is
    ever built; re-checked here purely so a stale/foreign key in an older
    persisted run can never crash this projection."""
    items: list[NextInvestigationDTO] = []
    for capability_key, value in priority.items():
        capability = capability_registry.get(capability_key)
        if capability is None:
            continue
        items.append(
            NextInvestigationDTO(
                capability=capability_key,
                label=capability.label,
                priority=round(max(0.0, min(1.0, value)), 4),
            )
        )
    items.sort(key=lambda item: item.priority, reverse=True)
    return items


def _map_reasoning(
    workspace: InvestigationWorkspace,
    investigation_priority: dict[str, float],
) -> ReasoningSummaryDTO:
    """Project the synthesis LLM's own scratch reasoning into the Context
    Explorer's Reasoning view. Pure — the same determinism guarantee every
    other helper in this module holds.

    `degraded` is read off `investigation_history`'s own code-authored
    entries (never LLM prose) rather than inferred from an empty hypothesis
    list, so a request that's legitimately too thin to have hypotheses
    (`has_reasoning=False`) is never mislabeled as a failure
    (`degraded=True`) — those are different, both real, outcomes.
    """
    strongest_index = _strongest_hypothesis_index(workspace.hypotheses)
    hypotheses = [
        _map_hypothesis(i, h, is_strongest=(i == strongest_index))
        for i, h in enumerate(workspace.hypotheses)
    ]
    contradictions = [_map_contradiction(i, c) for i, c in enumerate(workspace.contradictions)]
    resolved_count = sum(1 for c in contradictions if c.resolved)

    # `investigation_history` is code-authored (see `understanding.
    # _history_entry`), never LLM prose — the literal phrase "synthesis
    # degraded to a deterministic summary" is written there, and only
    # there, exactly when a synthesis round fell back
    # (`understanding.synthesize_engineering_understanding`'s `except`
    # branch). Checking `reasoning_notes` instead would be checking the
    # LLM's own text, which is never written on the fallback path at all —
    # the fallback constructs a fresh `InvestigationWorkspace` whose only
    # note is a fixed string, but it's `investigation_history` that carries
    # the reliable, per-round signal across multiple synthesis rounds.
    degraded = any(
        "synthesis degraded" in entry.lower() for entry in workspace.investigation_history
    )
    last_update = workspace.investigation_history[-1] if workspace.investigation_history else ""

    return ReasoningSummaryDTO(
        has_reasoning=bool(hypotheses or contradictions),
        degraded=degraded,
        hypotheses=hypotheses,
        contradictions=contradictions,
        open_contradiction_count=len(contradictions) - resolved_count,
        resolved_contradiction_count=resolved_count,
        strongest_hypothesis_id=(
            f"hyp_{strongest_index}" if strongest_index is not None else None
        ),
        dead_ends=list(workspace.dead_ends),
        next_investigation=_map_next_investigation(investigation_priority),
        last_update=last_update,
    )


def _map_planning(
    readiness: Readiness,
    factors: list[CapabilityFactor],
    blocking: list[str],
) -> PlanningAssessmentDTO:
    """Build planning assessment from readiness + capability factors."""
    reasons: list[PlanningFactorDTO] = []
    for f in factors:
        reasons.append(
            PlanningFactorDTO(satisfied=f.satisfied, description=f.label),
        )
    for reason in blocking:
        reasons.append(
            PlanningFactorDTO(satisfied=False, description=reason),
        )

    return PlanningAssessmentDTO(
        status=readiness,
        reasons=reasons,
    )
