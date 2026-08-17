"""Proves the RunCoordinator <-> Engineering State integration described
in `run_coordinator.py`'s own `_apply_agent_output`/
`_record_engineering_event` docstrings.

Two properties, both load-bearing:

1. **Opt-in, non-breaking**: every existing caller (which never passes
   `engineering_task_id`) behaves identically to before — no Engineering
   Event is written, `Run`/`AgentStep` persist exactly as they always
   have. Proven by `test_omitting_engineering_task_id_changes_nothing`.
2. **Atomic, authoritative**: when `engineering_task_id` IS given, the
   event and the run's status commit together (same transaction), and if
   the event append fails, the run is marked failed rather than silently
   reporting completion with no corresponding event — proven by
   `test_event_append_failure_fails_the_run_not_just_the_event` and
   `test_successful_run_commits_event_and_status_together`.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentOutput, Confidence
from app.engineering_state import events as ev_events
from app.engineering_state.materialize import fold
from app.models.agent_step import AgentStep
from app.models.engineering_event import EngineeringEvent
from app.models.run import Run
from app.orchestrator.run_coordinator import RunCoordinator
from app.repositories.engineering_event_repository import EngineeringEventRepository

pytestmark = pytest.mark.asyncio


def _stub_output(*, result: dict | None = None) -> AgentOutput:
    return AgentOutput(
        agent_id="test_agent",
        subject_id="subj-1",
        confidence=Confidence(score=0.9, reasoning="stub"),
        evidence=[],
        result=result or {"summary": "did the thing"},
    )


async def _seed_run_and_step(db: AsyncSession) -> tuple[Run, AgentStep]:
    run = Run(
        id=uuid.uuid4(),
        subject_id="subj-1",
        subject_type="freetext",
        display_name="test",
        goal="test_goal",
        status="running",
    )
    step = AgentStep(id=uuid.uuid4(), run_id=run.id, agent_id="test_agent", status="running")
    db.add(run)
    db.add(step)
    await db.flush()
    return run, step


async def test_omitting_engineering_task_id_changes_nothing(db_session: AsyncSession) -> None:
    """The overwhelming majority of existing callers pass no
    `engineering_task_id` at all — confirm `_apply_agent_output` behaves
    exactly as it did before this parameter existed: `run`/`step` persist,
    and this call does not create any new `EngineeringEvent` row.

    Compares a before/after row count rather than asserting the table is
    globally empty: this test's `db_session` fixture is one transaction
    against the same real, shared test database other test modules also
    commit real rows into (e.g. `test_engineering_events.py`'s
    concurrency test uses independently-committing sessions) — the table
    being non-empty in general is expected and correct; what must be
    proven is that *this specific call*, with no `engineering_task_id`,
    added nothing to it.
    """
    coordinator = RunCoordinator(db=db_session, registry=None)  # type: ignore[arg-type]
    run, step = await _seed_run_and_step(db_session)
    output = _stub_output()

    before = (await db_session.execute(select(func.count()).select_from(EngineeringEvent))).scalar()

    await coordinator._apply_agent_output(step, run, output, latency_ms=42, on_pre_commit=None)

    assert run.status == "completed"
    assert step.status == "completed"

    after = (await db_session.execute(select(func.count()).select_from(EngineeringEvent))).scalar()
    assert after == before


async def test_successful_run_commits_event_and_status_together(
    db_session: AsyncSession,
) -> None:
    coordinator = RunCoordinator(db=db_session, registry=None)  # type: ignore[arg-type]
    run, step = await _seed_run_and_step(db_session)
    output = _stub_output(result={"summary": "tests pass"})
    task_id = uuid.uuid4()

    await coordinator._apply_agent_output(
        step, run, output, latency_ms=42, on_pre_commit=None, engineering_task_id=task_id
    )

    assert run.status == "completed"
    assert step.status == "completed"

    repo = EngineeringEventRepository(db_session)
    events = await repo.list_for_task(task_id)
    assert len(events) == 1
    assert events[0].event_type == "ObservationRecorded"
    assert events[0].payload["raw_result"] == {"summary": "tests pass"}
    assert events[0].actor == f"legacy:run_coordinator:agent={step.agent_id}"

    state = fold(events)
    assert len(state.observations) == 1


async def test_event_append_failure_fails_the_run_not_just_the_event(
    db_session: AsyncSession,
) -> None:
    """The core invariant this integration exists to protect: an
    Engineering Event append failure must not leave `run.status` claiming
    success. Simulated by making the repository's `append` raise —
    exactly what a malformed payload or a genuine DB error would do."""
    coordinator = RunCoordinator(db=db_session, registry=None)  # type: ignore[arg-type]
    run, step = await _seed_run_and_step(db_session)
    output = _stub_output()
    task_id = uuid.uuid4()

    with patch.object(
        EngineeringEventRepository, "append", new=AsyncMock(side_effect=RuntimeError("boom"))
    ):
        await coordinator._apply_agent_output(
            step, run, output, latency_ms=42, on_pre_commit=None, engineering_task_id=task_id
        )

    # NOT "completed" — the run must honestly report that it could not
    # durably record its own outcome, exactly the same discipline a real
    # agent-execution failure already gets.
    assert run.status == "failed"
    assert step.status == "failed"
    assert run.error_message is not None
    assert "Engineering State event append failed" in run.error_message

    # And, consistently, no event exists for this task either — the
    # failure was real, not partially applied.
    repo = EngineeringEventRepository(db_session)
    assert await repo.list_for_task(task_id) == []


async def test_run_and_event_are_visible_together_after_commit(db_session: AsyncSession) -> None:
    """A concurrent reader (a different session) must never observe
    run.status == "completed" without the event also being visible, or
    vice versa — the two commit in the same transaction, so this is
    guaranteed by Postgres itself, not by application ordering. Checked
    here by committing for real (this test does not rely on the
    transactional `db_session` fixture's rollback) and reading back
    through a second, independent session."""
    from app.database.session import AsyncSessionLocal

    task_id = uuid.uuid4()
    run_id: uuid.UUID
    async with AsyncSessionLocal() as session:
        coordinator = RunCoordinator(db=session, registry=None)  # type: ignore[arg-type]
        run, step = await _seed_run_and_step(session)
        run_id = run.id
        output = _stub_output()
        await coordinator._apply_agent_output(
            step, run, output, latency_ms=1, on_pre_commit=None, engineering_task_id=task_id
        )
        # _commit_with_hook already committed; nothing further to do.

    async with AsyncSessionLocal() as verify_session:
        fetched_run = await verify_session.get(Run, run_id)
        assert fetched_run is not None
        assert fetched_run.status == "completed"

        repo = EngineeringEventRepository(verify_session)
        events = await repo.list_for_task(task_id)
        assert len(events) == 1


async def test_event_append_and_a_later_failed_write_roll_back_together() -> None:
    """Test C from the Phase 1 correction instructions, proven against
    the real Postgres transaction boundary, not a mock: within ONE
    session/transaction, the Engineering Event append succeeds (flushed,
    pending) and a SEPARATE write in that same transaction then fails at
    the database (a genuine constraint violation — a duplicate primary
    key — not a simulated exception). After the resulting rollback,
    neither the event nor the conflicting write exists. This is Postgres
    transactional atomicity itself, not application logic — the test
    exists to prove this repository's `append()` (add()+flush(), never
    commit — see its own docstring) actually participates in the
    caller's transaction rather than accidentally committing early.
    """
    from app.database.session import AsyncSessionLocal

    task_id = uuid.uuid4()
    duplicate_run_id = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        repo = EngineeringEventRepository(session)
        await repo.append(
            task_id=task_id,
            event_type=ev_events.GOAL_CREATED,
            payload={"description": "x", "postconditions": ["p"]},
            actor="test",
        )
        # Flushed, pending, visible within this transaction — but NOT
        # committed yet, exactly as append()'s docstring promises.

        session.add(
            Run(
                id=duplicate_run_id,
                subject_id="s",
                subject_type="freetext",
                display_name="d",
                goal="g",
                status="queued",
            )
        )
        session.add(
            Run(  # same primary key -> real IntegrityError on flush
                id=duplicate_run_id,
                subject_id="s2",
                subject_type="freetext",
                display_name="d2",
                goal="g2",
                status="queued",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()

        await session.rollback()

    async with AsyncSessionLocal() as verify_session:
        repo = EngineeringEventRepository(verify_session)
        assert await repo.list_for_task(task_id) == []
        assert await verify_session.get(Run, duplicate_run_id) is None
