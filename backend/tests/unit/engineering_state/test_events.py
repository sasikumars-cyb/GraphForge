"""Payload shape validation — `app.engineering_state.events`.

These are contract tests in the literal sense: each one is derived
directly from a MUST in `docs/graphforge/ENGINEERING_STATE_ARCHITECTURE.md`
(cited per test), not from an implementation detail.
"""

from __future__ import annotations

import pytest

from app.engineering_state import events as ev


def test_unrecognized_event_type_rejected() -> None:
    with pytest.raises(ev.InvalidEventPayloadError, match="Unrecognized event_type"):
        ev.validate_payload("SomeFuturePhaseEvent", {})


def test_goal_created_requires_checkable_postconditions() -> None:
    """ES: a Goal with no checkable postconditions can never establish
    Goal Satisfied — Capabilities contract §17's predicate has nothing to
    evaluate."""
    with pytest.raises(ev.InvalidEventPayloadError, match="postconditions"):
        ev.validate_payload(ev.GOAL_CREATED, {"description": "vague intent", "postconditions": []})

    # A real one is fine.
    ev.validate_payload(
        ev.GOAL_CREATED,
        {"description": "confirm tests pass", "postconditions": ["exit code == 0"]},
    )


def test_evidence_requires_origin_class_from_the_closed_set() -> None:
    """ES §4: origin_class is structural and closed — {world_fact,
    human_directive, repository_content}."""
    base = {
        "reference": "r",
        "summary": "s",
        "source_trust": "high",
        "capability": "c",
    }
    with pytest.raises(ev.InvalidEventPayloadError, match="origin_class"):
        ev.validate_payload(ev.EVIDENCE_RECORDED, {**base, "origin_class": "made_up"})

    ev.validate_payload(ev.EVIDENCE_RECORDED, {**base, "origin_class": "world_fact"})


def test_belief_requires_the_full_es_section_6_shape() -> None:
    """ES §6: confidence + uncertainty + evidence_sufficiency +
    qualitative_status + derivation_method, together — never confidence
    alone."""
    with pytest.raises(ev.InvalidEventPayloadError, match="missing required field"):
        ev.validate_payload(ev.BELIEF_RECORDED, {"proposition": "x", "confidence": 0.9})


def test_belief_confidence_must_be_a_valid_probability() -> None:
    payload = {
        "proposition": "x",
        "confidence": 1.5,
        "uncertainty": 0.1,
        "evidence_sufficiency": "adequate",
        "qualitative_status": "corroborated",
        "derivation_method": "evidence_derived",
        "evidence_ids": [],
    }
    with pytest.raises(ev.InvalidEventPayloadError, match=r"\[0.0, 1.0\]"):
        ev.validate_payload(ev.BELIEF_RECORDED, payload)


def test_belief_evidence_sufficiency_is_closed_vocabulary() -> None:
    payload = {
        "proposition": "x",
        "confidence": 0.5,
        "uncertainty": 0.1,
        "evidence_sufficiency": "totally sure",
        "qualitative_status": "corroborated",
        "derivation_method": "evidence_derived",
        "evidence_ids": [],
    }
    with pytest.raises(ev.InvalidEventPayloadError, match="evidence_sufficiency"):
        ev.validate_payload(ev.BELIEF_RECORDED, payload)


def test_belief_qualitative_status_is_closed_vocabulary() -> None:
    payload = {
        "proposition": "x",
        "confidence": 0.5,
        "uncertainty": 0.1,
        "evidence_sufficiency": "adequate",
        "qualitative_status": "vibes",
        "derivation_method": "evidence_derived",
        "evidence_ids": [],
    }
    with pytest.raises(ev.InvalidEventPayloadError, match="qualitative_status"):
        ev.validate_payload(ev.BELIEF_RECORDED, payload)


def test_observation_recorded_has_no_classification_field() -> None:
    """Deliberate scope boundary: Observation *classification* is
    Capabilities contract §16, Control-Plane-owned, deterministic, and
    does not exist until Phase 5. A Phase 1 ObservationRecorded payload
    is accepted without any classification field — and this test exists
    specifically to fail loudly if a future edit adds an unenforced
    'classification' key to the required set, which would let something
    other than the future Control Plane silently establish it."""
    ev.validate_payload(
        ev.OBSERVATION_RECORDED, {"raw_result": {"exit_code": 0}, "capability": "run_test_suite"}
    )


def test_decision_requires_alternatives_considered_as_a_list() -> None:
    """ES §12: a Decision without recorded alternatives is not
    meaningfully a Decision."""
    with pytest.raises(ev.InvalidEventPayloadError, match="alternatives_considered"):
        ev.validate_payload(
            ev.DECISION_MADE,
            {
                "selected_option": "x",
                "alternatives_considered": "not a list",
                "decision_maker": "role:x",
            },
        )


def test_every_declared_event_type_has_a_validator() -> None:
    """Guards against a new entry being added to EVENT_TYPES without a
    corresponding validator — every type in the closed vocabulary must be
    reachable through validate_payload, never fall through silently."""
    for event_type in ev.EVENT_TYPES:
        # Each of these is deliberately invalid (empty payload) — the
        # point is only that validate_payload() raises the SHAPE error,
        # not the "unrecognized event_type" error, proving a validator
        # exists and was actually invoked.
        with pytest.raises(ev.InvalidEventPayloadError) as exc_info:
            ev.validate_payload(event_type, {})
        assert "Unrecognized event_type" not in str(exc_info.value)
