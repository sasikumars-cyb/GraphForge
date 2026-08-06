"""The merge verdict rule — pure, total, and the only supported way to
produce a `MergeRecommendation`.

Why this is a function and not a prompt: the verdict is the one output of the
whole system that a human acts on directly, often without reading the evidence
underneath it. An LLM asked to weigh six confidence states and a list of
unresolved actions will produce a defensible-sounding verdict every time,
including the times it is wrong, and nothing about the output would reveal
which time this was. A rule reaches the same verdict for the same inputs
forever, and disagreeing with it is a code review rather than a re-roll.

The concrete bug this closes: a summary line reading "Safe to merge / Verified"
above a body reading "safe after Inventory approval". Those came from two
independently-written renderings of one situation. Here there is one verdict
field, derived once, and every surface renders that — a headline that
contradicts its own body is no longer a thing a renderer is capable of
producing.
"""

from __future__ import annotations

from app.decision.contracts import (
    AffectedEntity,
    MergeRecommendation,
    OpenQuestion,
    ReviewerAction,
)
from app.knowledge_engine.contracts.confidence import ConfidenceState

# States that mean "this entity is genuinely in the blast radius". `REJECTED`
# is deliberately absent: a rejected entity is one the platform hypothesized
# and then disproved, and it stays on the decision as an audit record of what
# was considered — but it must not influence the verdict, because "we checked
# and it is not affected" is the opposite of a risk signal.
_IN_SCOPE_EXCLUDED_STATES = frozenset({ConfidenceState.REJECTED})

# Confidence a claim must reach before it can ride along in an unconditional
# approval. Below this, the platform is saying "probably" — and "probably"
# is exactly what a reviewer should be told to check rather than told to trust.
_APPROVAL_STATES = frozenset({ConfidenceState.VERIFIED, ConfidenceState.HIGHLY_LIKELY})


def in_scope_entities(entities: tuple[AffectedEntity, ...]) -> tuple[AffectedEntity, ...]:
    """The entities that count toward the verdict — everything the platform
    has not affirmatively ruled out. Exposed rather than kept private because
    renderers need the same partition the rule used; a UI that showed rejected
    entities alongside live ones under one "Affected" heading would contradict
    the verdict computed from only the live ones.
    """
    return tuple(
        entity for entity in entities if entity.confidence.state not in _IN_SCOPE_EXCLUDED_STATES
    )


def derive_merge_recommendation(
    *,
    affected_entities: tuple[AffectedEntity, ...],
    open_questions: tuple[OpenQuestion, ...] = (),
    reviewer_actions: tuple[ReviewerAction, ...] = (),
) -> MergeRecommendation:
    """Compute the verdict. Total over every possible input, first match wins.

    The ordering encodes a single principle — *the weakest link decides* — and
    each branch is ordered by how bad the situation it describes is, not by how
    common it is:

    1. `CONFLICTING` on any in-scope entity → ``block``. Evidence pointing both
       ways about whether a change affects a service is strictly worse than no
       evidence: it means two sources the platform trusts disagree, and no
       amount of reviewer attention resolves that from inside the PR.
    2. An unresolved blocking action → ``approve_with_conditions``. The path
       forward is known and assigned; the merge is fine once it is done.
    3. Everything in scope at `VERIFIED`/`HIGHLY_LIKELY` and no safety-relevant
       unknown → ``approve``. Note this is vacuously true when nothing is
       affected, which is the correct verdict for a change that touches nothing
       downstream.
    4. Otherwise → ``request_changes``. This is the "we cannot assess this yet
       and nobody has been assigned to close the gap" case: an under-confident
       entity or a safety-relevant unknown with no action attached. It asks for
       work on the PR rather than pretending a confident read exists.
    """
    in_scope = in_scope_entities(affected_entities)

    conflicting = tuple(
        entity for entity in in_scope if entity.confidence.state == ConfidenceState.CONFLICTING
    )
    if conflicting:
        names = ", ".join(entity.entity_name for entity in conflicting)
        return MergeRecommendation(
            verdict="block",
            reasoning=(
                f"Contradictory evidence about whether this change affects: {names}. "
                "Sources the platform trusts disagree, so no safe merge call can be "
                "made from this pull request alone."
            ),
            blocking_conditions=tuple(entity.entity_id for entity in conflicting),
        )

    unresolved_blocking = tuple(
        action for action in reviewer_actions if action.blocking and not action.resolved
    )
    if unresolved_blocking:
        reasons = "; ".join(action.reason for action in unresolved_blocking)
        return MergeRecommendation(
            verdict="approve_with_conditions",
            reasoning=(
                f"Merge is conditional on {len(unresolved_blocking)} outstanding "
                f"action(s): {reasons}"
            ),
            blocking_conditions=tuple(action.action_id for action in unresolved_blocking),
        )

    under_confident = tuple(
        entity for entity in in_scope if entity.confidence.state not in _APPROVAL_STATES
    )
    safety_unknowns = tuple(question for question in open_questions if question.safety_relevant)

    if not under_confident and not safety_unknowns:
        if in_scope:
            return MergeRecommendation(
                verdict="approve",
                reasoning=(
                    f"All {len(in_scope)} affected entit"
                    f"{'y is' if len(in_scope) == 1 else 'ies are'} confirmed by "
                    "independent evidence, with no unresolved safety questions."
                ),
            )
        return MergeRecommendation(
            verdict="approve",
            reasoning=(
                "No downstream entities were found to be affected, and no unresolved "
                "safety questions remain."
            ),
        )

    parts: list[str] = []
    if under_confident:
        names = ", ".join(entity.entity_name for entity in under_confident)
        parts.append(f"insufficient evidence to confirm impact on: {names}")
    if safety_unknowns:
        parts.append(
            f"{len(safety_unknowns)} unresolved safety question(s): "
            + "; ".join(question.question for question in safety_unknowns)
        )
    return MergeRecommendation(
        verdict="request_changes",
        reasoning=(
            "This change cannot be assessed confidently yet — "
            + "; ".join(parts)
            + ". No reviewer action is assigned to close the gap."
        ),
        blocking_conditions=tuple(entity.entity_id for entity in under_confident),
    )
