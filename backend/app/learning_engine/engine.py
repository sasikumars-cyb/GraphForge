"""The Learning Engine proper: `build_learning_event` is the one place a
`RelationshipFeedback` becomes a `LearningEvent`. Pure and deterministic —
no I/O, no randomness, no wall-clock reads (the caller supplies
`created_at`/`event_id`) — so the exact same inputs always produce the
exact same event, byte for byte. This is what "reproducible learning
statistics" (Phase 5's requirement) rests on: statistics are computed from
these events, so if event construction were non-deterministic, nothing
built on top of it could be either.

This module never touches `app.knowledge_engine`, `app.indexer`, or
`app.graph` — it has no import of `HypothesisGenerator`, `KnowledgeValidator`,
`DefaultConfidenceEngine`, or the materializer, and cannot affect any of
them even by accident. It only ever reads a `RelationshipFeedback` and an
already-known prior confidence state, and returns a `LearningEvent`.
"""

from __future__ import annotations

from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.learning_engine.contracts.feedback import RelationshipFeedback
from app.learning_engine.contracts.learning_event import LearningEvent, LearningEventType

# Local to this module only — an ordinal ranking used solely to classify a
# human's confidence correction as "raised" vs. "lowered" trust. This is
# NOT the confidence engine's own state machine (that stays exactly as
# `DefaultConfidenceEngine` defines it, untouched) — it is a display/
# classification convenience for feedback, nothing computes confidence
# from it.
_CONFIDENCE_RANK: dict[ConfidenceState, int] = {
    ConfidenceState.REJECTED: 0,
    ConfidenceState.CONFLICTING: 1,
    ConfidenceState.CANDIDATE: 2,
    ConfidenceState.LIKELY: 3,
    ConfidenceState.HIGHLY_LIKELY: 4,
    ConfidenceState.VERIFIED: 5,
}

_KIND_TO_SIMPLE_EVENT_TYPE: dict[str, LearningEventType] = {
    "approve": "approved_relationship",
    "reject": "rejected_relationship",
    "flag_missing_relationship": "missing_relationship",
    "flag_weak_evidence": "weak_evidence",
    "flag_incorrect_explanation": "incorrect_explanation",
}


def _event_type_for_correction(
    corrected_state: ConfidenceState | None, confidence_state_before: str | None
) -> LearningEventType:
    """Only reached for kind='correct_confidence', where `corrected_state`
    is required non-None by `RelationshipFeedback.__post_init__`."""
    assert corrected_state is not None
    before_rank = (
        _CONFIDENCE_RANK[ConfidenceState(confidence_state_before)]
        if confidence_state_before is not None
        else _CONFIDENCE_RANK[ConfidenceState.CANDIDATE]
    )
    after_rank = _CONFIDENCE_RANK[corrected_state]
    return "high_confidence" if after_rank >= before_rank else "low_confidence"


def build_learning_event(
    feedback: RelationshipFeedback,
    *,
    event_id: str,
    confidence_state_before: str | None,
    generator_names: tuple[str, ...] = (),
) -> LearningEvent:
    """`confidence_state_before` is the judged relationship's
    `confidence_state` at the moment feedback was submitted — `None` only
    for `flag_missing_relationship`, where no relationship exists yet."""
    if feedback.kind == "correct_confidence":
        event_type: LearningEventType = _event_type_for_correction(
            feedback.corrected_state, confidence_state_before
        )
    else:
        event_type = _KIND_TO_SIMPLE_EVENT_TYPE[feedback.kind]

    relationship_key = None
    if feedback.relationship_type and feedback.source_entity and feedback.target_entity:
        relationship_key = (
            f"{feedback.repository_id}:{feedback.relationship_type}:"
            f"{feedback.source_entity}:{feedback.target_entity}"
        )

    return LearningEvent(
        id=event_id,
        repository_id=feedback.repository_id,
        event_type=event_type,
        source=feedback.source,
        detail=feedback.reason,
        created_at=feedback.created_at,
        relationship_key=relationship_key,
        relationship_type=feedback.relationship_type,
        generator_names=generator_names,
        confidence_state_at_event=confidence_state_before,
    )
