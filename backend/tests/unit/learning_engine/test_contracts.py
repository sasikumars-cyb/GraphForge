"""ADR 0018 RFC-07 — `LearningEvent`/`RelationshipFeedback` validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.contracts.correction import CorrectionSource
from app.learning_engine.contracts.feedback import RelationshipFeedback
from app.learning_engine.contracts.learning_event import LearningEvent


def _source() -> CorrectionSource:
    return CorrectionSource(kind="human", identity="user@example.com", trust_level=1.0)


def test_relationship_scoped_event_requires_relationship_key_and_type() -> None:
    with pytest.raises(ValueError, match="relationship_key and relationship_type"):
        LearningEvent(
            id="event:1",
            repository_id="repo:1",
            event_type="approved_relationship",
            source=_source(),
            detail="looks right",
            created_at=datetime.now(UTC),
        )


def test_missing_relationship_event_must_not_have_a_relationship_key() -> None:
    with pytest.raises(ValueError, match="must be None for missing_relationship"):
        LearningEvent(
            id="event:1",
            repository_id="repo:1",
            event_type="missing_relationship",
            source=_source(),
            detail="expected a CALLS_SERVICE edge here",
            created_at=datetime.now(UTC),
            relationship_key="repo:1:CALLS_SERVICE:a:b",
        )


def test_missing_relationship_event_constructs_without_a_relationship_key() -> None:
    event = LearningEvent(
        id="event:1",
        repository_id="repo:1",
        event_type="missing_relationship",
        source=_source(),
        detail="expected a CALLS_SERVICE edge here",
        created_at=datetime.now(UTC),
    )
    assert event.relationship_key is None


def test_feedback_targeting_kind_requires_relationship_reference() -> None:
    with pytest.raises(ValueError, match="are required for kind"):
        RelationshipFeedback(
            repository_id="repo:1",
            source=_source(),
            kind="approve",
            reason="confirmed by manifest",
            created_at=datetime.now(UTC),
        )


def test_flag_missing_relationship_must_not_reference_a_relationship() -> None:
    with pytest.raises(ValueError, match="must not reference a relationship"):
        RelationshipFeedback(
            repository_id="repo:1",
            source=_source(),
            kind="flag_missing_relationship",
            reason="expected an edge here",
            created_at=datetime.now(UTC),
            relationship_type="CALLS_SERVICE",
            source_entity="a",
            target_entity="b",
        )


def test_correct_confidence_requires_corrected_state() -> None:
    with pytest.raises(ValueError, match="corrected_state is required"):
        RelationshipFeedback(
            repository_id="repo:1",
            source=_source(),
            kind="correct_confidence",
            reason="should be verified",
            created_at=datetime.now(UTC),
            relationship_type="CALLS_SERVICE",
            source_entity="a",
            target_entity="b",
        )


def test_valid_correct_confidence_feedback_constructs() -> None:
    feedback = RelationshipFeedback(
        repository_id="repo:1",
        source=_source(),
        kind="correct_confidence",
        reason="should be verified",
        created_at=datetime.now(UTC),
        relationship_type="CALLS_SERVICE",
        source_entity="a",
        target_entity="b",
        corrected_state=ConfidenceState.VERIFIED,
    )
    assert feedback.corrected_state == ConfidenceState.VERIFIED
