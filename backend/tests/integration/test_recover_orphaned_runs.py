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


async def test_recover_orphaned_runs_marks_running_rows_failed(db_session: AsyncSession) -> None:
    orphaned = _make_run("running")
    completed = _make_run("completed")
    queued = _make_run("queued")
    db_session.add_all([orphaned, completed, queued])
    await db_session.flush()

    recovered_count = await recover_orphaned_runs(db_session)

    assert recovered_count == 1
    await db_session.refresh(orphaned)
    assert orphaned.status == "failed"
    assert orphaned.error_message == "Interrupted by server restart."
    assert orphaned.completed_at is not None

    # Anything not "running" is untouched.
    await db_session.refresh(completed)
    await db_session.refresh(queued)
    assert completed.status == "completed"
    assert queued.status == "queued"


async def test_recover_orphaned_runs_is_a_no_op_when_nothing_is_running(
    db_session: AsyncSession,
) -> None:
    assert await recover_orphaned_runs(db_session) == 0
