"""Real-database proof of the Phase 1 Engineering State foundation.

Covers exactly the properties `docs/graphforge/
ENGINEERING_STATE_ARCHITECTURE.md` and the final adversarial sequencing
review require to be provable against a real Postgres, not merely
asserted:

- append-only (DB-level, not just application discipline)
- deterministic replay from the database, not just from in-memory objects
  (see `tests/unit/engineering_state/test_materialize.py` for the pure
  fold proof — this file proves the round trip through real persistence)
- concurrent appends to the same task cannot silently overwrite history
- an invalid payload never reaches the database at all
- Phase 1 Final Correctness Audit correction: a causally dependent event
  (GoalUpdated, PlanCreated, PlanStepCreated, or a BeliefRecorded citing
  nonexistent Evidence) cannot be durably appended if its referenced
  causal event does not exist, in this task, of the expected type —
  proven here at the real `append()` boundary, not merely at fold time.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import AsyncSessionLocal
from app.engineering_state import events as ev
from app.engineering_state.materialize import fold
from app.repositories.engineering_event_repository import (
    CausalOrderViolationError,
    EngineeringEventRepository,
)

pytestmark = pytest.mark.asyncio


def _goal_payload(description: str = "confirm tests pass") -> dict:
    return {"description": description, "postconditions": ["exit code == 0"]}


async def test_append_and_list_round_trip(db_session: AsyncSession) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()

    event = await repo.append(
        task_id=task_id,
        event_type=ev.GOAL_CREATED,
        payload=_goal_payload(),
        actor="test:harness",
    )
    await db_session.flush()

    assert event.sequence_number == 1
    assert event.task_id == task_id

    fetched = await repo.list_for_task(task_id)
    assert len(fetched) == 1
    assert fetched[0].id == event.id


async def test_sequence_numbers_are_monotonic_per_task(db_session: AsyncSession) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()

    first = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    second = await repo.append(
        task_id=task_id,
        event_type=ev.GOAL_UPDATED,
        payload={"goal_event_id": str(first.id), "description": "revised"},
        actor="t",
        causation_event_id=first.id,
    )

    assert first.sequence_number == 1
    assert second.sequence_number == 2


async def test_invalid_payload_never_reaches_the_database(db_session: AsyncSession) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()

    with pytest.raises(ev.InvalidEventPayloadError):
        await repo.append(
            task_id=task_id,
            event_type=ev.GOAL_CREATED,
            payload={"description": "no postconditions"},  # missing required field
            actor="t",
        )

    # Nothing was appended for this task at all.
    assert await repo.list_for_task(task_id) == []


async def test_database_replay_matches_in_memory_fold(db_session: AsyncSession) -> None:
    """The same property `test_materialize.py` proves in memory, proven
    here through a real INSERT + SELECT round trip: the events actually
    persisted, fetched back, fold to the state the payloads describe."""
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()

    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await repo.append(
        task_id=task_id,
        event_type=ev.EVIDENCE_RECORDED,
        payload={
            "reference": "r",
            "summary": "s",
            "origin_class": "world_fact",
            "source_trust": "high",
            "capability": "run_test_suite",
        },
        actor="t",
    )

    events = await repo.list_for_task(task_id)
    state = fold(events)

    assert state.event_count == 2
    assert state.goal is not None
    assert state.goal.event_id == goal.id
    assert len(state.evidence) == 1


async def test_update_is_rejected_at_the_database_level(db_session: AsyncSession) -> None:
    """Not application discipline — a raw UPDATE statement, bypassing the
    repository entirely, must still be refused by the database itself."""
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    event = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await db_session.flush()

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(
            text("UPDATE engineering_events SET actor = 'tampered' WHERE id = :id"),
            {"id": str(event.id)},
        )


async def test_delete_is_rejected_at_the_database_level(db_session: AsyncSession) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    event = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await db_session.flush()

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(
            text("DELETE FROM engineering_events WHERE id = :id"), {"id": str(event.id)}
        )


async def test_concurrent_appends_to_the_same_task_do_not_collide() -> None:
    """Two independent, really-committing sessions (not the shared,
    rolled-back `db_session` fixture — this needs genuine concurrency,
    the same pattern `tests/conftest.py` documents using for background
    execution) appending to the SAME task_id at the same time must not
    produce duplicate or skipped sequence_numbers. Each session takes its
    own `pg_advisory_xact_lock`, so one waits for the other rather than
    racing."""
    task_id = uuid.uuid4()

    async def append_one(n: int) -> None:
        async with AsyncSessionLocal() as session:
            repo = EngineeringEventRepository(session)
            # EvidenceRecorded specifically: no causal requirement (see
            # events.CAUSAL_REQUIREMENTS), so this test stays focused on
            # concurrency/sequencing alone, not on also satisfying a
            # causal-parent reference for each of the 10 events.
            await repo.append(
                task_id=task_id,
                event_type=ev.EVIDENCE_RECORDED,
                payload={
                    "reference": f"r{n}",
                    "summary": f"concurrent evidence {n}",
                    "origin_class": "world_fact",
                    "source_trust": "high",
                    "capability": "test",
                },
                actor=f"concurrent:{n}",
            )
            await session.commit()

    await asyncio.gather(*(append_one(n) for n in range(10)))

    async with AsyncSessionLocal() as session:
        repo = EngineeringEventRepository(session)
        events = await repo.list_for_task(task_id)

    sequence_numbers = sorted(e.sequence_number for e in events)
    assert sequence_numbers == list(range(1, 11)), (
        f"Expected exactly sequence numbers 1..10 with no gaps or "
        f"duplicates, got {sequence_numbers}"
    )


async def test_unique_constraint_is_a_real_backstop_not_just_documentation(
    db_session: AsyncSession,
) -> None:
    """Bypass the repository's advisory lock entirely and attempt to
    INSERT a duplicate (task_id, sequence_number) directly — the UNIQUE
    constraint must still refuse it. This is the "hard backstop" the
    repository's own docstring promises, proven independently of the
    repository's own serialization logic."""
    task_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO engineering_events "
            "(id, task_id, sequence_number, event_type, schema_version, payload, actor) "
            "VALUES (:id1, :task_id, 1, 'GoalCreated', 1, "
            '\'{"description": "a", "postconditions": ["p"]}\', \'test\')'
        ),
        {"id1": str(uuid.uuid4()), "task_id": str(task_id)},
    )
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO engineering_events "
                "(id, task_id, sequence_number, event_type, schema_version, payload, actor) "
                "VALUES (:id2, :task_id, 1, 'GoalCreated', 1, "
                '\'{"description": "b", "postconditions": ["p"]}\', \'test\')'
            ),
            {"id2": str(uuid.uuid4()), "task_id": str(task_id)},
        )


# --- Phase 1 correction: causal-reference validation at append() -----------
#
# Test A from the correction instructions: child-before-parent must be
# rejected, not silently accepted.


async def test_goal_updated_without_a_prior_goal_created_is_rejected(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()

    with pytest.raises(CausalOrderViolationError, match="requires causation_event_id"):
        await repo.append(
            task_id=task_id,
            event_type=ev.GOAL_UPDATED,
            payload={"goal_event_id": str(uuid.uuid4()), "description": "revised"},
            actor="test",
            # No causation_event_id given — nothing to validate the
            # payload's goal_event_id against, and there is no
            # GoalCreated for this task at all yet.
        )

    assert await repo.list_for_task(task_id) == []


async def test_goal_updated_referencing_a_nonexistent_goal_created_is_rejected(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    fabricated_parent_id = uuid.uuid4()  # never appended

    with pytest.raises(CausalOrderViolationError, match="does not exist"):
        await repo.append(
            task_id=task_id,
            event_type=ev.GOAL_UPDATED,
            payload={"goal_event_id": str(fabricated_parent_id), "description": "revised"},
            actor="test",
            causation_event_id=fabricated_parent_id,
        )


async def test_plan_step_created_before_plan_created_is_rejected(
    db_session: AsyncSession,
) -> None:
    """The exact scenario named in the correction instructions:
    PlanStepCreated -> PlanCreated (child before parent)."""
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    fabricated_plan_id = uuid.uuid4()  # no PlanCreated exists for this id

    with pytest.raises(CausalOrderViolationError, match="does not exist"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_CREATED,
            payload={
                "plan_event_id": str(fabricated_plan_id),
                "description": "run tests",
                "postcondition": "tests exit 0",
            },
            actor="test",
            causation_event_id=fabricated_plan_id,
        )

    # Confirm it was genuinely rejected, not partially applied.
    assert await repo.list_for_task(task_id) == []


async def test_causation_event_id_wrong_task_is_rejected(db_session: AsyncSession) -> None:
    """A real GoalCreated event exists, but in a DIFFERENT task —
    referencing it must fail exactly as if it didn't exist at all, per
    ES §14's tenant/task isolation discipline generalized here."""
    repo = EngineeringEventRepository(db_session)
    other_task_id = uuid.uuid4()
    goal = await repo.append(
        task_id=other_task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await db_session.flush()

    this_task_id = uuid.uuid4()
    with pytest.raises(CausalOrderViolationError, match="different task"):
        await repo.append(
            task_id=this_task_id,
            event_type=ev.GOAL_UPDATED,
            payload={"goal_event_id": str(goal.id), "description": "revised"},
            actor="test",
            causation_event_id=goal.id,
        )


async def test_causation_event_id_wrong_event_type_is_rejected(db_session: AsyncSession) -> None:
    """A real event exists, in the right task, but is the wrong TYPE to
    be this event's causal parent — e.g. a GoalUpdated citing an
    ObservationRecorded as though it were a GoalCreated."""
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    observation = await repo.append(
        task_id=task_id,
        event_type=ev.OBSERVATION_RECORDED,
        payload={"raw_result": {}, "capability": "x"},
        actor="t",
    )
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="not one of"):
        await repo.append(
            task_id=task_id,
            event_type=ev.GOAL_UPDATED,
            payload={"goal_event_id": str(observation.id), "description": "revised"},
            actor="test",
            causation_event_id=observation.id,
        )


async def test_payload_reference_disagreeing_with_causation_event_id_is_rejected(
    db_session: AsyncSession,
) -> None:
    """The exact inconsistency the audit flagged as the deepest gap: two
    independent "what caused this" fields (the payload's goal_event_id
    and the model's causation_event_id) silently allowed to disagree."""
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="disagreeing claims"):
        await repo.append(
            task_id=task_id,
            event_type=ev.GOAL_UPDATED,
            payload={"goal_event_id": str(uuid.uuid4()), "description": "revised"},  # mismatched
            actor="test",
            causation_event_id=goal.id,
        )


async def test_belief_citing_nonexistent_evidence_is_rejected(db_session: AsyncSession) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    fabricated_evidence_id = uuid.uuid4()

    with pytest.raises(CausalOrderViolationError, match="does not exist"):
        await repo.append(
            task_id=task_id,
            event_type=ev.BELIEF_RECORDED,
            payload={
                "proposition": "x",
                "confidence": 0.5,
                "uncertainty": 0.1,
                "evidence_sufficiency": "adequate",
                "qualitative_status": "corroborated",
                "derivation_method": "evidence_derived",
                "evidence_ids": [str(fabricated_evidence_id)],
            },
            actor="test",
        )


async def test_belief_with_no_evidence_is_legitimately_allowed(db_session: AsyncSession) -> None:
    """ES §4: a Belief MAY exist with zero supporting Evidence (a bare
    hypothesis) — this is a legitimate epistemic state, not something the
    causal-reference correction should start rejecting."""
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()

    belief = await repo.append(
        task_id=task_id,
        event_type=ev.BELIEF_RECORDED,
        payload={
            "proposition": "x",
            "confidence": 0.3,
            "uncertainty": 0.5,
            "evidence_sufficiency": "none",
            "qualitative_status": "speculative",
            "derivation_method": "inferred",
            "evidence_ids": [],
        },
        actor="test",
    )
    assert belief.sequence_number == 1


async def test_belief_citing_wrong_event_type_as_evidence_is_rejected(
    db_session: AsyncSession,
) -> None:
    """A real event exists, but it's a GoalCreated, not an
    EvidenceRecorded — citing it as evidence must fail."""
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="not 'EvidenceRecorded'"):
        await repo.append(
            task_id=task_id,
            event_type=ev.BELIEF_RECORDED,
            payload={
                "proposition": "x",
                "confidence": 0.5,
                "uncertainty": 0.1,
                "evidence_sufficiency": "adequate",
                "qualitative_status": "corroborated",
                "derivation_method": "evidence_derived",
                "evidence_ids": [str(goal.id)],
            },
            actor="test",
        )


# --- Phase 6: Plan supersession / PlanStep dependency / invalidation ------


async def test_plan_supersedes_nonexistent_plan_is_rejected(db_session: AsyncSession) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="does not exist"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_CREATED,
            payload={
                "goal_event_id": str(goal.id),
                "scope": ["repo-a"],
                "supersedes_plan_event_id": str(uuid.uuid4()),
            },
            actor="t",
            causation_event_id=goal.id,
        )


async def test_plan_supersedes_a_different_tasks_plan_is_rejected(db_session: AsyncSession) -> None:
    repo = EngineeringEventRepository(db_session)
    other_task_id = uuid.uuid4()
    other_goal = await repo.append(
        task_id=other_task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    other_plan = await repo.append(
        task_id=other_task_id,
        event_type=ev.PLAN_CREATED,
        payload={"goal_event_id": str(other_goal.id), "scope": ["repo-a"]},
        actor="t",
        causation_event_id=other_goal.id,
    )
    await db_session.flush()

    this_task_id = uuid.uuid4()
    this_goal = await repo.append(
        task_id=this_task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="different task"):
        await repo.append(
            task_id=this_task_id,
            event_type=ev.PLAN_CREATED,
            payload={
                "goal_event_id": str(this_goal.id),
                "scope": ["repo-a"],
                "supersedes_plan_event_id": str(other_plan.id),
            },
            actor="t",
            causation_event_id=this_goal.id,
        )


async def test_plan_supersedes_a_non_plan_event_is_rejected(db_session: AsyncSession) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="not 'PlanCreated'"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_CREATED,
            payload={
                "goal_event_id": str(goal.id),
                "scope": ["repo-a"],
                "supersedes_plan_event_id": str(goal.id),
            },
            actor="t",
            causation_event_id=goal.id,
        )


async def test_replan_produces_a_valid_new_plan_version_superseding_the_old(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    plan_a = await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_CREATED,
        payload={"goal_event_id": str(goal.id), "scope": ["repo-a"]},
        actor="t",
        causation_event_id=goal.id,
    )
    await db_session.flush()

    plan_b = await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_CREATED,
        payload={
            "goal_event_id": str(goal.id),
            "scope": ["repo-a"],
            "supersedes_plan_event_id": str(plan_a.id),
        },
        actor="t",
        causation_event_id=goal.id,
    )

    state = fold(await repo.list_for_task(task_id))
    assert len(state.plans) == 2
    from app.engineering_state.materialize import superseded_plan_event_ids

    assert superseded_plan_event_ids(state) == frozenset({plan_a.id})
    assert plan_b.id not in superseded_plan_event_ids(state)


async def test_plan_step_depends_on_nonexistent_step_is_rejected(db_session: AsyncSession) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    plan = await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_CREATED,
        payload={"goal_event_id": str(goal.id), "scope": ["repo-a"]},
        actor="t",
        causation_event_id=goal.id,
    )
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="does not exist"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_CREATED,
            payload={
                "plan_event_id": str(plan.id),
                "description": "d",
                "postcondition": "x",
                "depends_on": [str(uuid.uuid4())],
            },
            actor="t",
            causation_event_id=plan.id,
        )


async def test_plan_step_depends_on_a_non_planstep_event_is_rejected(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    plan = await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_CREATED,
        payload={"goal_event_id": str(goal.id), "scope": ["repo-a"]},
        actor="t",
        causation_event_id=goal.id,
    )
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="not 'PlanStepCreated'"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_CREATED,
            payload={
                "plan_event_id": str(plan.id),
                "description": "d",
                "postcondition": "x",
                "depends_on": [str(goal.id)],
            },
            actor="t",
            causation_event_id=plan.id,
        )


async def _plan_step_chain(repo: EngineeringEventRepository, task_id: uuid.UUID) -> Any:
    """Goal -> Plan -> PlanStep, returning `(goal, plan, step)` real,
    durably-appended events — the shared setup every PlanStepInvalidated
    test below builds on."""
    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    plan = await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_CREATED,
        payload={"goal_event_id": str(goal.id), "scope": ["repo-a"]},
        actor="t",
        causation_event_id=goal.id,
    )
    step = await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_STEP_CREATED,
        payload={"plan_event_id": str(plan.id), "description": "d", "postcondition": "x"},
        actor="t",
        causation_event_id=plan.id,
    )
    return goal, plan, step


async def test_plan_step_invalidated_before_plan_step_created_is_rejected(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    fabricated_step_id = uuid.uuid4()

    with pytest.raises(CausalOrderViolationError, match="does not exist"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(fabricated_step_id),
                "contradiction_observation_event_id": str(uuid.uuid4()),
                "reason": "x",
            },
            actor="t",
            causation_event_id=fabricated_step_id,
        )


async def test_plan_step_invalidated_citing_nonexistent_observation_is_rejected(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    _, _, step = await _plan_step_chain(repo, task_id)
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="does not exist"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(step.id),
                "contradiction_observation_event_id": str(uuid.uuid4()),
                "reason": "x",
            },
            actor="t",
            causation_event_id=step.id,
        )


async def test_plan_step_invalidated_citing_a_non_observation_event_is_rejected(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    goal, _, step = await _plan_step_chain(repo, task_id)
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="not 'ObservationRecorded'"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(step.id),
                "contradiction_observation_event_id": str(goal.id),
                "reason": "x",
            },
            actor="t",
            causation_event_id=step.id,
        )


async def test_plan_step_invalidated_citing_a_non_contradiction_observation_is_rejected(
    db_session: AsyncSession,
) -> None:
    """ES §10: 'Contradiction... is the ONLY classification that may
    trigger Replan.' An `Expected` Observation must not be usable to
    invalidate a PlanStep."""
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    _, _, step = await _plan_step_chain(repo, task_id)
    expected_observation = await repo.append(
        task_id=task_id,
        event_type=ev.OBSERVATION_RECORDED,
        payload={
            "raw_result": {"ok": True},
            "capability": "query_knowledge_graph",
            "outcome": "completed",
            "classification": "expected",
        },
        actor="t",
    )
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="ONLY classification"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(step.id),
                "contradiction_observation_event_id": str(expected_observation.id),
                "reason": "x",
            },
            actor="t",
            causation_event_id=step.id,
        )


async def test_plan_step_invalidated_citing_an_unclassified_observation_is_rejected(
    db_session: AsyncSession,
) -> None:
    """An Observation with no `classification` at all (e.g. a
    pre-Phase-5 producer, or one halted at outcome_unknown) must not be
    usable to invalidate a PlanStep either."""
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    _, _, step = await _plan_step_chain(repo, task_id)
    unclassified_observation = await repo.append(
        task_id=task_id,
        event_type=ev.OBSERVATION_RECORDED,
        payload={"raw_result": {"exit_code": 0}, "capability": "run_test_suite"},
        actor="t",
    )
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="ONLY classification"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(step.id),
                "contradiction_observation_event_id": str(unclassified_observation.id),
                "reason": "x",
            },
            actor="t",
            causation_event_id=step.id,
        )


async def test_plan_step_invalidated_citing_a_different_tasks_contradiction_is_rejected(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    other_task_id = uuid.uuid4()
    other_contradiction = await repo.append(
        task_id=other_task_id,
        event_type=ev.OBSERVATION_RECORDED,
        payload={
            "raw_result": {},
            "capability": "query_knowledge_graph",
            "outcome": "completed",
            "classification": "contradiction",
        },
        actor="t",
    )
    await db_session.flush()

    task_id = uuid.uuid4()
    _, _, step = await _plan_step_chain(repo, task_id)
    await db_session.flush()

    with pytest.raises(CausalOrderViolationError, match="different task"):
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(step.id),
                "contradiction_observation_event_id": str(other_contradiction.id),
                "reason": "x",
            },
            actor="t",
            causation_event_id=step.id,
        )


async def test_plan_step_invalidated_with_a_genuine_contradiction_succeeds(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    _, _, step = await _plan_step_chain(repo, task_id)
    contradiction = await repo.append(
        task_id=task_id,
        event_type=ev.OBSERVATION_RECORDED,
        payload={
            "raw_result": {"note": "falsified"},
            "capability": "query_knowledge_graph",
            "outcome": "completed",
            "classification": "contradiction",
        },
        actor="t",
    )
    await db_session.flush()

    invalidation = await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_STEP_INVALIDATED,
        payload={
            "plan_step_event_id": str(step.id),
            "contradiction_observation_event_id": str(contradiction.id),
            "reason": "postcondition falsified",
        },
        actor="t",
        causation_event_id=step.id,
    )

    state = fold(await repo.list_for_task(task_id))
    from app.engineering_state.materialize import is_plan_step_invalidated

    assert is_plan_step_invalidated(state, step.id) is True
    assert invalidation.causation_event_id == step.id


# --- Valid causal histories must keep working (correction instruction #4) --


async def test_valid_goal_created_then_updated_still_materializes(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await repo.append(
        task_id=task_id,
        event_type=ev.GOAL_UPDATED,
        payload={"goal_event_id": str(goal.id), "description": "revised"},
        actor="t",
        causation_event_id=goal.id,
    )

    state = fold(await repo.list_for_task(task_id))
    assert state.goal is not None
    assert state.goal.description == "revised"


async def test_valid_goal_plan_planstep_decision_chain_still_materializes(
    db_session: AsyncSession,
) -> None:
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()

    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    plan = await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_CREATED,
        payload={"goal_event_id": str(goal.id), "scope": ["repo-a"]},
        actor="t",
        causation_event_id=goal.id,
    )
    await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_STEP_CREATED,
        payload={
            "plan_event_id": str(plan.id),
            "description": "run tests",
            "postcondition": "tests exit 0",
        },
        actor="t",
        causation_event_id=plan.id,
    )
    await repo.append(
        task_id=task_id,
        event_type=ev.DECISION_MADE,
        payload={
            "selected_option": "proceed",
            "alternatives_considered": ["escalate"],
            "decision_maker": "test",
        },
        actor="t",
    )

    state = fold(await repo.list_for_task(task_id))
    assert state.goal is not None
    assert len(state.plans) == 1
    assert len(state.plan_steps) == 1
    assert len(state.decisions) == 1


# --- Test B (correction instructions): no hidden cache ---------------------


async def test_independent_fetches_of_the_same_history_fold_identically(
    db_session: AsyncSession,
) -> None:
    """Two INDEPENDENT calls to list_for_task (not the same Python list
    object reused) must fold to equal states — proving reconstruction
    depends only on what's actually persisted, never on anything cached
    in the repository, the session, or the fold function itself."""
    repo = EngineeringEventRepository(db_session)
    task_id = uuid.uuid4()
    goal = await repo.append(
        task_id=task_id, event_type=ev.GOAL_CREATED, payload=_goal_payload(), actor="t"
    )
    await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_CREATED,
        payload={"goal_event_id": str(goal.id), "scope": ["repo-a"]},
        actor="t",
        causation_event_id=goal.id,
    )

    history_1 = await repo.list_for_task(task_id)
    history_2 = await repo.list_for_task(task_id)  # a second, independent fetch

    assert history_1 is not history_2  # genuinely two separate query results
    assert fold(history_1) == fold(history_2)
