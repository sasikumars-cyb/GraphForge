"""Integration test for the startup orphaned-run recovery (see
app.orchestrator.background_execution.recover_orphaned_runs, wired into
app.main's lifespan).

Uses the rollback-based `db_session` fixture directly (no HTTP layer) —
this is a plain read-then-update-then-commit against real Run rows, none
of the cross-connection concerns that test_background_execution_api.py's
`client`-fixture tests exist to cover.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run
from app.orchestrator.background_execution import recover_orphaned_runs

pytestmark = pytest.mark.asyncio


def _make_run(status: str) -> Run:
    return Run(
        id=uuid.uuid4(),
        subject_id="freetext:orphan-test",
        subject_type="freetext",
        display_name="Orphan test",
        goal="plan_freeform",
        status=status,
    )


async def test_recover_orphaned_runs_marks_running_and_queued_rows_failed(
    db_session: AsyncSession,
) -> None:
    # Regression test: "queued" used to be left untouched here, on the
    # (wrong) assumption that only "running" rows could be orphaned by a
    # restart. But create_pending_run commits a row at status="queued"
    # *before* schedule_run_execution ever creates the asyncio task that
    # would advance it to "running" (see run_coordinator.py) — a restart
    # landing in that gap (e.g. the dev server's --reload firing mid
    # request) leaves the row at "queued" forever, with no task left to
    # run it and no recovery path catching it. Reproduced for real: 7 rows
    # stuck this way in a live dev database after a reload during active
    # backend editing, started_at/completed_at both null.
    running = _make_run("running")
    queued = _make_run("queued")
    completed = _make_run("completed")
    failed = _make_run("failed")
    db_session.add_all([running, queued, completed, failed])
    await db_session.flush()

    recovered_count = await recover_orphaned_runs(db_session)

    assert recovered_count == 2
    for run in (running, queued):
        await db_session.refresh(run)
        assert run.status == "failed"
        assert run.error_message == "Interrupted by server restart."
        assert run.completed_at is not None

    # Anything already terminal is untouched.
    await db_session.refresh(completed)
    await db_session.refresh(failed)
    assert completed.status == "completed"
    assert failed.status == "failed"
    assert failed.error_message is None


async def test_recover_orphaned_runs_is_a_no_op_when_nothing_is_running(
    db_session: AsyncSession,
) -> None:
    assert await recover_orphaned_runs(db_session) == 0
