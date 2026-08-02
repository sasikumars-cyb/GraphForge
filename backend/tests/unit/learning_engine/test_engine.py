"""ADR 0018 RFC-07 — `build_learning_event`: pure, deterministic mapping
from `RelationshipFeedback` to `LearningEvent`."""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.contracts.correction import CorrectionSource
from app.learning_engine.contracts.feedback import RelationshipFeedback
from app.learning_engine.engine import build_learning_event

_SOURCE = CorrectionSource(kind="human", identity="user@example.com", trust_level=1.0)
_NOW = datetime.now(UTC)


def _feedback(**overrides: object) -> RelationshipFeedback:
    defaults: dict[str, object] = dict(
        repository_id="repo:1",
        source=_SOURCE,
        kind="approve",
        reason="confirmed by manifest",
        created_at=_NOW,
        relationship_type="CALLS_SERVICE",
        source_entity="a",
        target_entity="b",
    )
    defaults.update(overrides)
    return RelationshipFeedback(**defaults)  # type: ignore[arg-type]


def test_approve_maps_to_approved_relationship() -> None:
    event = build_learning_event(
        _feedback(kind="approve"),
        event_id="event:1",
        confidence_state_before="likely",
    )
    assert event.event_type == "approved_relationship"
    assert event.relationship_key == "repo:1:CALLS_SERVICE:a:b"


def test_reject_maps_to_rejected_relationship() -> None:
    event = build_learning_event(
        _feedback(kind="reject"), event_id="event:1", confidence_state_before="likely"
    )
    assert event.event_type == "rejected_relationship"


def test_correct_confidence_upward_maps_to_high_confidence() -> None:
    event = build_learning_event(
        _feedback(kind="correct_confidence", corrected_state=ConfidenceState.VERIFIED),
        event_id="event:1",
        confidence_state_before="candidate",
    )
    assert event.event_type == "high_confidence"


def test_correct_confidence_downward_maps_to_low_confidence() -> None:
    event = build_learning_event(
        _feedback(kind="correct_confidence", corrected_state=ConfidenceState.CANDIDATE),
        event_id="event:1",
        confidence_state_before="verified",
    )
    assert event.event_type == "low_confidence"


def test_flag_missing_relationship_has_no_relationship_key() -> None:
    feedback = RelationshipFeedback(
        repository_id="repo:1",
        source=_SOURCE,
        kind="flag_missing_relationship",
        reason="expected an OWNS_DATABASE edge",
        created_at=_NOW,
    )
    event = build_learning_event(feedback, event_id="event:1", confidence_state_before=None)
    assert event.event_type == "missing_relationship"
    assert event.relationship_key is None
    assert event.relationship_type is None


def test_flag_weak_evidence_and_incorrect_explanation_map_correctly() -> None:
    weak = build_learning_event(
        _feedback(kind="flag_weak_evidence"), event_id="event:1", confidence_state_before="likely"
    )
    incorrect = build_learning_event(
        _feedback(kind="flag_incorrect_explanation"),
        event_id="event:2",
        confidence_state_before="likely",
    )
    assert weak.event_type == "weak_evidence"
    assert incorrect.event_type == "incorrect_explanation"


def test_generator_names_pass_through_unchanged() -> None:
    event = build_learning_event(
        _feedback(kind="approve"),
        event_id="event:1",
        confidence_state_before="likely",
        generator_names=("deterministic_parser", "frontier_llm_generator"),
    )
    assert event.generator_names == ("deterministic_parser", "frontier_llm_generator")


def test_deterministic_same_inputs_produce_identical_event() -> None:
    first = build_learning_event(
        _feedback(kind="reject"), event_id="event:1", confidence_state_before="likely"
    )
    second = build_learning_event(
        _feedback(kind="reject"), event_id="event:1", confidence_state_before="likely"
    )
    assert first == second
