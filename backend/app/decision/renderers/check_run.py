"""GitHub Check Run projection.

The thinnest and most safety-critical renderer: a branch protection rule reads
`conclusion` and decides whether a merge button is clickable. It is a total,
hardcoded mapping from `MergeRecommendation.verdict` with no independent
judgement of its own — a check that could reach a different conclusion than the
PR comment above it would be a second opinion masquerading as an enforcement
mechanism.

`summary` is the decision's own `reasoning` string verbatim, not a re-worded
version, for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.decision.contracts import EngineeringDecision, MergeVerdict

CheckConclusion = Literal["success", "neutral", "action_required", "failure"]

# Total over MergeVerdict. Kept as data rather than an if-chain so that adding a
# verdict without deciding its enforcement semantics fails at the lookup below
# instead of silently falling through to a permissive default.
_VERDICT_TO_CONCLUSION: dict[MergeVerdict, CheckConclusion] = {
    "approve": "success",
    # `neutral` rather than `success`: conditions are outstanding, so the check
    # must not read as a clean pass - but nor should it read as a failure, since
    # the path forward is known and assigned.
    "approve_with_conditions": "neutral",
    "request_changes": "action_required",
    "block": "failure",
}

_VERDICT_TO_TITLE: dict[MergeVerdict, str] = {
    "approve": "No unconfirmed impact",
    "approve_with_conditions": "Approve with conditions",
    "request_changes": "Insufficient evidence to assess",
    "block": "Contradictory evidence — blocked",
}


@dataclass(frozen=True)
class CheckRunAnnotation:
    """One outstanding condition, surfaced where GitHub shows check details."""

    condition_id: str
    message: str


@dataclass(frozen=True)
class CheckRunProjection:
    """What a caller hands to GitHub's Check Runs API. Deliberately a plain
    value object rather than an API call: the projection is testable without a
    network, and the transport stays somebody else's concern."""

    name: str
    conclusion: CheckConclusion
    title: str
    summary: str
    annotations: tuple[CheckRunAnnotation, ...] = field(default_factory=tuple)

    @property
    def gate_passed(self) -> bool:
        """What a branch-protection rule reads.

        `neutral` is deliberately not a pass. An approval carrying unresolved
        blocking conditions has not had those conditions met yet; treating it
        as passing would let the gate contradict the very conditions it is
        reporting. Clearing them produces a *new* decision whose verdict is
        `approve` - resolution is a new fact, never a status flip on an
        existing record.
        """
        return self.conclusion == "success"


def render_check_run(decision: EngineeringDecision) -> CheckRunProjection:
    """Project a decision onto a GitHub Check Run. Pure."""
    recommendation = decision.merge_recommendation
    verdict = recommendation.verdict

    entities_by_id = {entity.entity_id: entity for entity in decision.affected_entities}
    actions_by_id = {action.action_id: action for action in decision.reviewer_actions}

    annotations: list[CheckRunAnnotation] = []
    for condition_id in recommendation.blocking_conditions:
        # Referential integrity is guaranteed by EngineeringDecision's own
        # __post_init__, so a condition always resolves to one of these two.
        if condition_id in actions_by_id:
            action = actions_by_id[condition_id]
            annotations.append(
                CheckRunAnnotation(
                    condition_id=condition_id,
                    message=f"{action.target}: {action.reason}",
                )
            )
        else:
            entity = entities_by_id[condition_id]
            annotations.append(
                CheckRunAnnotation(
                    condition_id=condition_id,
                    message=(
                        f"{entity.entity_name}: impact is {entity.confidence.state.value}, "
                        "not independently confirmed"
                    ),
                )
            )

    return CheckRunProjection(
        name="GraphForge Impact Check",
        conclusion=_VERDICT_TO_CONCLUSION[verdict],
        title=_VERDICT_TO_TITLE[verdict],
        summary=recommendation.reasoning,
        annotations=tuple(annotations),
    )
