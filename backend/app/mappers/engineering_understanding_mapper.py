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
- relevant_areas ← graph_topics + graph_components (group by topic)
- known_constraints ← .constraints (list copy)
- missing_information ← gap_summaries + remaining_unknowns (dedup)
- unknowns ← remaining + rejected + unavailable (categorise)
- evidence_summary ← EvidencePackage.items by tier (summarise)
- recommendations ← insights + risks (prefix "Risk: ")
- planning_assessment ← readiness + factors + blocking (checklist)
- confidence_explanation ← factors (completed/outstanding prose)
- documentation_status ← pass-through (caller-derived)
- next_step ← pass-through (caller-derived)
- debug_bundle ← pass-through (None when include_debug=False)
"""

from __future__ import annotations

from collections import defaultdict
from typing import get_args

from app.context_pipeline.reasoning.curation import EvidencePackage, Tier
from app.schemas.engineering_understanding import (
    AreaClusterDTO,
    CapabilityFactor,
    ComponentProjection,
    EngineeringUnderstandingDTO,
    PlanningAssessmentDTO,
    PlanningFactorDTO,
    ProjectionInput,
    Readiness,
    RepositorySummaryDTO,
    TopicProjection,
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

    relevant_areas = _map_areas(p.graph_topics, p.graph_components)

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

    return EngineeringUnderstandingDTO(
        business_goal=business_goal,
        current_situation=current_situation,
        expected_outcome=expected_outcome,
        repository_summary=repository_summary,
        architecture_summary=architecture_summary,
        relevant_areas=relevant_areas,
        known_constraints=known_constraints,
        missing_information=missing_information,
        unknowns=unknowns,
        evidence_summary=evidence_summary,
        recommendations=recommendations,
        planning_assessment=planning_assessment,
        confidence_explanation=confidence_explanation,
        documentation_status=p.documentation_status,
        next_step=p.next_step,
        debug_bundle=p.debug_bundle if include_debug else None,
    )


# ---------------------------------------------------------------------------
# Extracted helpers (non-trivial)
# ---------------------------------------------------------------------------


def _map_areas(
    topics: list[TopicProjection],
    components: list[ComponentProjection],
) -> list[AreaClusterDTO]:
    """Group components by topic (case-insensitive) into area clusters.

    Components whose ``topic`` field does not match any topic name are
    collected into an ``"Other"`` bucket.  Empty component names are
    silently skipped.
    """
    if not topics and not components:
        return []

    # Build topic name lookup (case-insensitive)
    topic_names: dict[str, str] = {}
    for t in topics:
        if t.name:
            topic_names[t.name.lower()] = t.name

    # Group components by topic
    groups: dict[str, list[str]] = defaultdict(list)
    for c in components:
        if not c.name:
            continue
        comp_topic = c.topic.lower()
        if comp_topic in topic_names:
            groups[topic_names[comp_topic]].append(c.name)
        else:
            groups["Other"].append(c.name)

    # Add topics that had no components (so they still appear)
    for canonical in topic_names.values():
        if canonical not in groups:
            groups[canonical] = []

    return [
        AreaClusterDTO(name=name, components=comps)
        for name, comps in groups.items()
    ]


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
