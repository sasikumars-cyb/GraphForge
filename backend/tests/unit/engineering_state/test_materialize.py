"""`app.engineering_state.materialize.fold` — deterministic replay.

Engineering State contract §9: "the materialized Engineering State at
any historical point in time MUST be exactly reconstructable by folding
the event log up to that point." This is the direct proof, and it
requires no database at all: `fold()` is a pure function over plain
`EngineeringEvent` objects, never persisted, never queried — exactly the
property that makes it trustworthy (nothing about the reconstruction
depends on database state, timing, or I/O).
"""

from __future__ import annotations

import uuid

import pytest

from app.engineering_state import events as ev
from app.engineering_state.materialize import MixedTaskEventsError, fold
from app.models.engineering_event import EngineeringEvent


def _event(
    *,
    task_id: uuid.UUID,
    sequence_number: int,
    event_type: str,
    payload: dict,
    actor: str = "test",
) -> EngineeringEvent:
    """An in-memory-only `EngineeringEvent` — never added to a session,
    never flushed. Plain SQLAlchemy model instantiation needs no
    database; only persistence would."""
    return EngineeringEvent(
        id=uuid.uuid4(),
        task_id=task_id,
        sequence_number=sequence_number,
        event_type=event_type,
        schema_version=1,
        payload=payload,
        actor=actor,
    )


def _sample_events(task_id: uuid.UUID) -> list[EngineeringEvent]:
    goal_payload = {
        "description": "confirm the test suite currently passes at revision R",
        "postconditions": ["exit code == 0"],
    }
    goal = _event(
        task_id=task_id, sequence_number=1, event_type=ev.GOAL_CREATED, payload=goal_payload
    )

    plan_payload = {"goal_event_id": str(goal.id), "scope": ["repo-a"]}
    plan = _event(
        task_id=task_id, sequence_number=2, event_type=ev.PLAN_CREATED, payload=plan_payload
    )

    step_payload = {
        "plan_event_id": str(plan.id),
        "description": "run the test suite",
        "postcondition": "the test suite exits 0",
    }
    step = _event(
        task_id=task_id,
        sequence_number=3,
        event_type=ev.PLAN_STEP_CREATED,
        payload=step_payload,
    )

    evidence_payload = {
        "reference": "pytest-exit-code",
        "summary": "exit code 0",
        "origin_class": "world_fact",
        "source_trust": "high",
        "capability": "run_test_suite",
    }
    evidence = _event(
        task_id=task_id,
        sequence_number=4,
        event_type=ev.EVIDENCE_RECORDED,
        payload=evidence_payload,
    )

    belief_payload = {
        "proposition": "tests currently pass",
        "confidence": 0.95,
        "uncertainty": 0.05,
        "evidence_sufficiency": "adequate",
        "qualitative_status": "corroborated",
        "derivation_method": "evidence_derived",
        "evidence_ids": [str(evidence.id)],
    }
    belief = _event(
        task_id=task_id,
        sequence_number=5,
        event_type=ev.BELIEF_RECORDED,
        payload=belief_payload,
    )

    decision_payload = {
        "selected_option": "report tests passing",
        "alternatives_considered": ["escalate to human"],
        "decision_maker": "role:reasoning_engine",
    }
    decision = _event(
        task_id=task_id,
        sequence_number=6,
        event_type=ev.DECISION_MADE,
        payload=decision_payload,
    )

    observation_payload = {"raw_result": {"exit_code": 0}, "capability": "run_test_suite"}
    observation = _event(
        task_id=task_id,
        sequence_number=7,
        event_type=ev.OBSERVATION_RECORDED,
        payload=observation_payload,
    )

    return [goal, plan, step, evidence, belief, decision, observation]


def test_fold_reconstructs_every_record_type() -> None:
    task_id = uuid.uuid4()
    events = _sample_events(task_id)

    state = fold(events)

    assert state.task_id == task_id
    assert state.event_count == 7
    assert state.goal is not None
    assert state.goal.description == "confirm the test suite currently passes at revision R"
    assert len(state.plans) == 1
    assert len(state.plan_steps) == 1
    assert len(state.evidence) == 1
    assert len(state.beliefs) == 1
    assert len(state.decisions) == 1
    assert len(state.observations) == 1


def test_fold_derives_canonical_order_from_sequence_number_not_list_order() -> None:
    """Phase 1 Final Correctness Audit correction: this test previously
    was named/described as proving "order independence", which
    overclaimed. What it actually proves, precisely: `fold()` derives its
    processing order from each event's `sequence_number` — never from the
    order the caller's Python list happens to iterate in. Both calls
    below process the exact same causal order internally
    (sequence_number 1, 2, 3, ...); only the *list's* iteration order
    differs between them.

    This test does NOT prove, and must never be read as proving, that
    reordering the events' actual `sequence_number` values (a different
    causal history) would produce the same result — it would not, and
    should not: see `test_child_before_parent_sequence_would_be_invalid_
    if_it_existed` below for why the architecture requires this, and
    `tests/integration/test_engineering_events.py`'s causal-rejection
    tests for where that invalid history is now actually prevented
    (append time, not fold time)."""
    task_id = uuid.uuid4()
    events = _sample_events(task_id)

    forward = fold(events)
    backward = fold(list(reversed(events)))

    assert forward == backward


def test_child_before_parent_sequence_would_be_invalid_if_it_existed() -> None:
    """Documents, at the fold level, exactly why causal order has real
    semantic meaning — the property the previous test's old name and
    docstring obscured. `fold()` itself performs no causal validation
    (that is now `EngineeringEventRepository.append()`'s job, enforced
    before such a history can become durable); this test constructs the
    malformed history directly, in memory, to prove `fold()` would
    silently mishandle it if one ever reached it, which is precisely the
    reasoning that justified moving the check upstream rather than
    leaving it here."""
    task_id = uuid.uuid4()
    goal_updated = _event(
        task_id=task_id,
        sequence_number=1,
        event_type=ev.GOAL_UPDATED,
        payload={"goal_event_id": str(uuid.uuid4()), "description": "revised"},
    )
    goal_created = _event(
        task_id=task_id,
        sequence_number=2,
        event_type=ev.GOAL_CREATED,
        payload={"description": "original", "postconditions": ["p1"]},
    )

    state = fold([goal_updated, goal_created])

    # The update, having been processed first (per its lower
    # sequence_number) with no goal yet to update, is silently absorbed
    # into nothing — the final state shows only GoalCreated's own
    # content, with no trace the update was ever attempted. This is the
    # exact silent-corruption behavior EngineeringEventRepository.append()
    # now refuses to let become durable history in the first place.
    assert state.goal is not None
    assert state.goal.description == "original"
    assert state.goal.event_id == goal_created.id


def test_fold_replays_identically_on_repeated_calls() -> None:
    """Same event history -> same reconstructed state, called twice."""
    task_id = uuid.uuid4()
    events = _sample_events(task_id)

    first = fold(events)
    second = fold(events)

    assert first == second


def test_goal_updated_overlays_without_losing_original_event_provenance() -> None:
    task_id = uuid.uuid4()
    created = _event(
        task_id=task_id,
        sequence_number=1,
        event_type=ev.GOAL_CREATED,
        payload={"description": "original", "postconditions": ["p1"]},
    )
    updated = _event(
        task_id=task_id,
        sequence_number=2,
        event_type=ev.GOAL_UPDATED,
        payload={"goal_event_id": str(created.id), "description": "revised"},
    )

    state = fold([created, updated])

    assert state.goal is not None
    assert state.goal.description == "revised"
    # postconditions weren't in the update payload — must be carried
    # forward unchanged, never dropped just because the update omitted them.
    assert state.goal.postconditions == ("p1",)
    # The GoalUpdated event's own id, not GoalCreated's — the fold always
    # reflects the LATEST relevant event, per the fold's own documented
    # semantics.
    assert state.goal.event_id == updated.id


def test_fold_rejects_mixed_task_events() -> None:
    task_a, task_b = uuid.uuid4(), uuid.uuid4()
    event_a = _event(
        task_id=task_a,
        sequence_number=1,
        event_type=ev.GOAL_CREATED,
        payload={"description": "a", "postconditions": ["p"]},
    )
    event_b = _event(
        task_id=task_b,
        sequence_number=1,
        event_type=ev.GOAL_CREATED,
        payload={"description": "b", "postconditions": ["p"]},
    )

    with pytest.raises(MixedTaskEventsError):
        fold([event_a, event_b])


def test_fold_requires_at_least_one_event() -> None:
    with pytest.raises(ValueError, match="at least one event"):
        fold([])


# --- Phase 3: Authorization Grant lifecycle replay --------------------------


def test_fold_reconstructs_a_granted_but_not_yet_consumed_grant() -> None:
    task_id = uuid.uuid4()
    action_id = uuid.uuid4()
    granted = _event(
        task_id=task_id,
        sequence_number=1,
        event_type=ev.AUTHORIZATION_GRANTED,
        payload={
            "grant_id": "biz-id-1",
            "action_id": str(action_id),
            "capability_id": "query_knowledge_graph",
            "capability_version": 1,
            "policy_version_id": "policy-1",
            "scope": "neo4j read-only",
            "safety_validity_result": {"valid": True, "reason": "ok"},
            "novelty": "known",
            "issued_at": "2026-08-17T00:00:00+00:00",
            "ttl_seconds": 60,
            "human_approval_id": None,
        },
    )

    state = fold([granted])

    assert len(state.authorization_grants) == 1
    record = state.authorization_grants[0]
    assert record.grant_event_id == granted.id
    assert record.action_id == action_id
    assert record.state == "granted"


def test_fold_reconstructs_the_full_grant_consumed_lifecycle() -> None:
    task_id = uuid.uuid4()
    action_id = uuid.uuid4()
    granted = _event(
        task_id=task_id,
        sequence_number=1,
        event_type=ev.AUTHORIZATION_GRANTED,
        payload={
            "grant_id": "biz-id-1",
            "action_id": str(action_id),
            "capability_id": "query_knowledge_graph",
            "capability_version": 1,
            "policy_version_id": "policy-1",
            "scope": "neo4j read-only",
            "safety_validity_result": {"valid": True, "reason": "ok"},
            "novelty": "known",
            "issued_at": "2026-08-17T00:00:00+00:00",
            "ttl_seconds": 60,
            "human_approval_id": None,
        },
    )
    consuming = _event(
        task_id=task_id,
        sequence_number=2,
        event_type=ev.AUTHORIZATION_CONSUMING,
        payload={"grant_event_id": str(granted.id), "action_id": str(action_id)},
    )
    consumed = _event(
        task_id=task_id,
        sequence_number=3,
        event_type=ev.AUTHORIZATION_CONSUMED,
        payload={
            "grant_event_id": str(granted.id),
            "consuming_event_id": str(consuming.id),
            "action_id": str(action_id),
            "tool_id": "neo4j_graph",
        },
    )

    state = fold([granted, consuming, consumed])

    # One record, not three — the fold reflects CURRENT state, not a raw
    # event dump.
    assert len(state.authorization_grants) == 1
    record = state.authorization_grants[0]
    assert record.state == "consumed"
    assert record.grant_event_id == granted.id


def test_fold_reconstructs_an_invalidated_grant() -> None:
    task_id = uuid.uuid4()
    action_id = uuid.uuid4()
    granted = _event(
        task_id=task_id,
        sequence_number=1,
        event_type=ev.AUTHORIZATION_GRANTED,
        payload={
            "grant_id": "biz-id-1",
            "action_id": str(action_id),
            "capability_id": "query_knowledge_graph",
            "capability_version": 1,
            "policy_version_id": "policy-1",
            "scope": "neo4j read-only",
            "safety_validity_result": {"valid": True, "reason": "ok"},
            "novelty": "known",
            "issued_at": "2026-08-17T00:00:00+00:00",
            "ttl_seconds": 60,
            "human_approval_id": None,
        },
    )
    invalidated = _event(
        task_id=task_id,
        sequence_number=2,
        event_type=ev.AUTHORIZATION_INVALIDATED,
        payload={
            "grant_event_id": str(granted.id),
            "action_id": str(action_id),
            "reason": "policy changed",
            "invalidated_by_policy_version_id": "policy-2",
        },
    )

    state = fold([granted, invalidated])

    assert state.authorization_grants[0].state == "invalidated"


def test_fold_reconstructs_denials_separately_from_grants() -> None:
    task_id = uuid.uuid4()
    denied = _event(
        task_id=task_id,
        sequence_number=1,
        event_type=ev.AUTHORIZATION_DENIED,
        payload={
            "denial_stage": "policy_denial",
            "reason": "no ALLOW rule",
            "action_id": None,
            "capability_id": "query_knowledge_graph",
        },
    )

    state = fold([denied])

    assert state.authorization_grants == ()
    assert len(state.authorization_denials) == 1
    assert state.authorization_denials[0].denial_stage == "policy_denial"


def test_fold_grant_lifecycle_is_order_derived_from_sequence_number() -> None:
    """Same guarantee as the Goal/Plan tests above, extended to the
    Authorization Grant lifecycle: handing `fold()` the same events in a
    different Python list order produces the identical reconstructed
    state, because the canonical order is `sequence_number`, not list
    position."""
    task_id = uuid.uuid4()
    action_id = uuid.uuid4()
    granted = _event(
        task_id=task_id,
        sequence_number=1,
        event_type=ev.AUTHORIZATION_GRANTED,
        payload={
            "grant_id": "biz-id-1",
            "action_id": str(action_id),
            "capability_id": "query_knowledge_graph",
            "capability_version": 1,
            "policy_version_id": "policy-1",
            "scope": "neo4j read-only",
            "safety_validity_result": {"valid": True, "reason": "ok"},
            "novelty": "known",
            "issued_at": "2026-08-17T00:00:00+00:00",
            "ttl_seconds": 60,
            "human_approval_id": None,
        },
    )
    consuming = _event(
        task_id=task_id,
        sequence_number=2,
        event_type=ev.AUTHORIZATION_CONSUMING,
        payload={"grant_event_id": str(granted.id), "action_id": str(action_id)},
    )

    forward = fold([granted, consuming])
    reversed_input = fold([consuming, granted])

    assert forward.authorization_grants[0].state == reversed_input.authorization_grants[0].state
    assert forward.authorization_grants[0].state == "consuming"
