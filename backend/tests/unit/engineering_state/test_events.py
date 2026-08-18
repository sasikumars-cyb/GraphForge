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


def test_goal_created_user_id_is_optional() -> None:
    """Phase 7.3 ownership fix — a Goal created before this field existed
    has no `user_id`, and that MUST remain a valid, permanently-supported
    shape (Ownership Design Audit §4/§7), never a required field."""
    ev.validate_payload(
        ev.GOAL_CREATED,
        {"description": "no owner recorded", "postconditions": ["p"]},
    )


def test_goal_created_user_id_accepts_a_real_uuid() -> None:
    ev.validate_payload(
        ev.GOAL_CREATED,
        {
            "description": "owned goal",
            "postconditions": ["p"],
            "user_id": "11111111-1111-1111-1111-111111111111",
        },
    )


def test_goal_created_user_id_rejects_a_non_uuid_value() -> None:
    """Fail closed at write time — an unparseable `user_id` would
    silently defeat the ownership check at read time otherwise."""
    with pytest.raises(ev.InvalidEventPayloadError, match="user_id"):
        ev.validate_payload(
            ev.GOAL_CREATED,
            {"description": "bad owner", "postconditions": ["p"], "user_id": "not-a-uuid"},
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


def test_observation_recorded_classification_remains_optional() -> None:
    """Phase 5 adds `outcome`/`classification` as OPTIONAL fields (Cap
    §16), never required — `app.orchestrator.run_coordinator`'s existing,
    independent producer supplies neither, and must remain valid. This
    test exists specifically to fail loudly if a future edit moves either
    field into the required set, which would break that real producer."""
    ev.validate_payload(
        ev.OBSERVATION_RECORDED, {"raw_result": {"exit_code": 0}, "capability": "run_test_suite"}
    )


def test_observation_recorded_outcome_is_closed_vocabulary() -> None:
    base = {"raw_result": {"exit_code": 0}, "capability": "run_test_suite"}
    with pytest.raises(ev.InvalidEventPayloadError, match="outcome"):
        ev.validate_payload(ev.OBSERVATION_RECORDED, {**base, "outcome": "made_up"})

    ev.validate_payload(ev.OBSERVATION_RECORDED, {**base, "outcome": "completed"})
    ev.validate_payload(ev.OBSERVATION_RECORDED, {**base, "outcome": "outcome_unknown"})


def test_observation_recorded_classification_is_closed_vocabulary() -> None:
    """Cap §16.1's five-way vocabulary, minus `blocked` — a Blocked
    Observation never reaches this event type (see
    `app.control_plane.observation_classification`'s module docstring)."""
    base = {"raw_result": {"exit_code": 0}, "capability": "run_test_suite"}
    with pytest.raises(ev.InvalidEventPayloadError, match="classification"):
        ev.validate_payload(ev.OBSERVATION_RECORDED, {**base, "classification": "blocked"})
    with pytest.raises(ev.InvalidEventPayloadError, match="classification"):
        ev.validate_payload(ev.OBSERVATION_RECORDED, {**base, "classification": "made_up"})

    for valid in ("expected", "anomaly", "uncertain_outcome", "contradiction"):
        ev.validate_payload(ev.OBSERVATION_RECORDED, {**base, "classification": valid})


def test_plan_step_created_requires_a_postcondition() -> None:
    """Cap §15.1: Independent Verification resolves the postcondition it
    evaluates ONLY from the immutable `PlanStepCreated` event — it must
    exist, or there is nothing to pin."""
    with pytest.raises(ev.InvalidEventPayloadError, match="postcondition"):
        ev.validate_payload(
            ev.PLAN_STEP_CREATED,
            {"plan_event_id": "x", "description": "run the test suite"},
        )
    with pytest.raises(ev.InvalidEventPayloadError, match="postcondition"):
        ev.validate_payload(
            ev.PLAN_STEP_CREATED,
            {"plan_event_id": "x", "description": "run the test suite", "postcondition": "   "},
        )

    ev.validate_payload(
        ev.PLAN_STEP_CREATED,
        {
            "plan_event_id": "x",
            "description": "run the test suite",
            "postcondition": "the test suite exits 0",
        },
    )


def test_plan_created_supersedes_field_is_optional_but_validated_when_present() -> None:
    """ES §11: 'Any revision after approval MUST produce a new Plan
    version; the approved version MUST remain retrievable unchanged.'
    `supersedes_plan_event_id` is OPTIONAL (a base Plan supersedes
    nothing) but must be a non-empty str when present."""
    base = {"goal_event_id": "g", "scope": ["repo-a"]}
    ev.validate_payload(ev.PLAN_CREATED, base)  # no supersedes — fine.
    ev.validate_payload(ev.PLAN_CREATED, {**base, "supersedes_plan_event_id": "prior-event-id"})
    with pytest.raises(ev.InvalidEventPayloadError, match="supersedes_plan_event_id"):
        ev.validate_payload(ev.PLAN_CREATED, {**base, "supersedes_plan_event_id": "   "})
    with pytest.raises(ev.InvalidEventPayloadError, match="supersedes_plan_event_id"):
        ev.validate_payload(ev.PLAN_CREATED, {**base, "supersedes_plan_event_id": 123})


def test_plan_step_created_depends_on_is_optional_but_must_be_a_list() -> None:
    """ES §11: the minimum dependency-edge representation — optional,
    most PlanSteps depend on nothing."""
    base = {"plan_event_id": "p", "description": "d", "postcondition": "x exits 0"}
    ev.validate_payload(ev.PLAN_STEP_CREATED, base)  # no depends_on — fine.
    ev.validate_payload(ev.PLAN_STEP_CREATED, {**base, "depends_on": []})
    ev.validate_payload(ev.PLAN_STEP_CREATED, {**base, "depends_on": ["a", "b"]})
    with pytest.raises(ev.InvalidEventPayloadError, match="depends_on"):
        ev.validate_payload(ev.PLAN_STEP_CREATED, {**base, "depends_on": "not-a-list"})


def test_plan_step_invalidated_requires_the_full_shape() -> None:
    """ES §10: `contradiction_observation_event_id` is what makes this a
    genuinely evidenced fact — required, never optional."""
    with pytest.raises(ev.InvalidEventPayloadError, match="missing required field"):
        ev.validate_payload(ev.PLAN_STEP_INVALIDATED, {"plan_step_event_id": "s"})

    ev.validate_payload(
        ev.PLAN_STEP_INVALIDATED,
        {
            "plan_step_event_id": "s",
            "contradiction_observation_event_id": "o",
            "reason": "postcondition falsified",
        },
    )


def test_plan_step_invalidated_reason_must_be_non_empty() -> None:
    with pytest.raises(ev.InvalidEventPayloadError, match="reason"):
        ev.validate_payload(
            ev.PLAN_STEP_INVALIDATED,
            {
                "plan_step_event_id": "s",
                "contradiction_observation_event_id": "o",
                "reason": "   ",
            },
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
