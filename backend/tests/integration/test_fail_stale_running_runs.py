"""Integration test for the periodic stale-run sweep (P2 orphan-run
detection — see app.orchestrator.background_execution.
fail_stale_running_runs, wired into app.main's lifespan as
run_stale_run_sweep_forever).

Uses the rollback-based `db_session` fixture directly (no HTTP layer),
same pattern test_recover_orphaned_runs.py uses — a plain
read-then-update-then-commit against real Run rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run
from app.orchestrator.background_execution import fail_stale_running_runs

pytestmark = pytest.mark.asyncio


def _make_run(status: str, *, started_at: datetime | None) -> Run:
    return Run(
        id=uuid.uuid4(),
        subject_id="freetext:stale-test",
        subject_type="freetext",
        display_name="Stale run test",
        goal="plan_freeform",
        status=status,
        started_at=started_at,
    )


async def test_fails_a_running_run_older_than_the_threshold(db_session: AsyncSession) -> None:
    old_running = _make_run("running", started_at=datetime.now(UTC) - timedelta(minutes=30))
    db_session.add(old_running)
    await db_session.flush()

    count = await fail_stale_running_runs(db_session, older_than=timedelta(minutes=20))

    await db_session.refresh(old_running)
    assert count == 1
    assert old_running.status == "failed"
    assert old_running.error_message is not None
    assert "stalled" in old_running.error_message.lower()
    assert old_running.completed_at is not None


async def test_leaves_a_recent_running_run_alone(db_session: AsyncSession) -> None:
    recent_running = _make_run("running", started_at=datetime.now(UTC) - timedelta(minutes=2))
    db_session.add(recent_running)
    await db_session.flush()

    count = await fail_stale_running_runs(db_session, older_than=timedelta(minutes=20))

    await db_session.refresh(recent_running)
    assert count == 0
    assert recent_running.status == "running"
    assert recent_running.error_message is None


async def test_never_touches_queued_or_awaiting_input(db_session: AsyncSession) -> None:
    # "queued" is recover_orphaned_runs'/the job queue's own lease-expiry
    # concern, not a wall-clock one — and "awaiting_input" may genuinely
    # sit for a long time waiting on a human, which is not staleness.
    old_queued = _make_run("queued", started_at=datetime.now(UTC) - timedelta(hours=2))
    old_awaiting_input = _make_run(
        "awaiting_input", started_at=datetime.now(UTC) - timedelta(hours=2)
    )
    db_session.add_all([old_queued, old_awaiting_input])
    await db_session.flush()

    count = await fail_stale_running_runs(db_session, older_than=timedelta(minutes=20))

    await db_session.refresh(old_queued)
    await db_session.refresh(old_awaiting_input)
    assert count == 0
    assert old_queued.status == "queued"
    assert old_awaiting_input.status == "awaiting_input"


async def test_never_touches_a_run_with_no_started_at(db_session: AsyncSession) -> None:
    # A "running" row should always have started_at set (RunCoordinator
    # sets it in the same commit as the status transition), but this must
    # degrade safely rather than crash/misfire if one somehow doesn't.
    never_started = _make_run("running", started_at=None)
    db_session.add(never_started)
    await db_session.flush()

    count = await fail_stale_running_runs(db_session, older_than=timedelta(minutes=20))

    await db_session.refresh(never_started)
    assert count == 0
    assert never_started.status == "running"


async def test_returns_zero_and_commits_nothing_when_nothing_is_stale(
    db_session: AsyncSession,
) -> None:
    count = await fail_stale_running_runs(db_session, older_than=timedelta(minutes=20))
    assert count == 0
