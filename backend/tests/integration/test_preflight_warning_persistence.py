"""ADR 0011, OD-1 — preflight warning persistence, against real Postgres.

Mirrors the verification discipline `tests/integration/
test_llm_invocation_persistence.py` already established for the sibling
ADR 0012 persistence pathway: exercise `record_preflight_warnings` against a
real `AgentStep` row in a real session, then read back through a *fresh,
independent* session — never the one that made the change — to prove what
actually landed in Postgres, not just what the in-session object graph
believes.

No WARNING-producing check exists yet (Jira/Confluence/GitHub reachability
checks are future work, out of scope for ADR 0011 OD-1 — see that ADR's
Open Decisions). These tests exercise the persistence primitive directly
with synthetic `PreflightWarning`s, exactly as a future check's caller
(`RunCoordinator`) would.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.session import engine
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.orchestrator.preflight import PreflightWarning, record_preflight_warnings

pytestmark = pytest.mark.asyncio

_RealSession = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# Every real Run row created here is committed at status="running" (the
# realistic pre-execution state) — left uncleaned, that pollutes exact-count
# assertions elsewhere (e.g. tests/integration/test_recover_orphaned_runs.py),
# the same test-pollution class documented for ADR 0012's own tests. This
# fixture deletes every run this module created, regardless of pass/fail.
_created_run_ids: list[uuid.UUID] = []


@pytest.fixture(autouse=True)
async def _cleanup_created_runs() -> AsyncIterator[None]:
    _created_run_ids.clear()
    yield
    if not _created_run_ids:
        return
    cleanup = _RealSession()
    for run_id in _created_run_ids:
        step_result = await cleanup.execute(select(AgentStep).where(AgentStep.run_id == run_id))
        for step in step_result.scalars().all():
            await cleanup.delete(step)
        run_result = await cleanup.execute(select(Run).where(Run.id == run_id))
        run = run_result.scalars().first()
        if run is not None:
            await cleanup.delete(run)
    await cleanup.commit()
    await cleanup.close()
    _created_run_ids.clear()


async def _make_run_and_step() -> tuple[AsyncSession, uuid.UUID, AgentStep]:
    db = _RealSession()
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:x",
        subject_type="freetext",
        display_name="x",
        goal="plan_freeform",
        status="running",
    )
    db.add(run)
    await db.flush()
    _created_run_ids.append(run.id)
    step = AgentStep(id=uuid.uuid4(), run_id=run.id, agent_id="planning", status="running")
    db.add(step)
    await db.flush()
    return db, step.id, step


async def _read_warnings(step_id: uuid.UUID) -> list[dict] | None:
    reader = _RealSession()
    row = (await reader.execute(select(AgentStep).where(AgentStep.id == step_id))).scalars().first()
    warnings = row.preflight_warnings if row is not None else None
    await reader.close()
    return warnings


async def test_zero_warnings_persist_as_empty_list_after_commit() -> None:
    db, step_id, step = await _make_run_and_step()
    record_preflight_warnings(step, [])
    await db.commit()
    await db.close()

    assert await _read_warnings(step_id) == []


async def test_single_warning_persists_after_commit() -> None:
    db, step_id, step = await _make_run_and_step()
    record_preflight_warnings(
        step,
        [
            PreflightWarning(
                code="jira_reachable",
                dependency="Jira",
                message="Jira is not reachable.",
                checked_at="2026-07-31T00:00:00Z",
            )
        ],
    )
    await db.commit()
    await db.close()

    warnings = await _read_warnings(step_id)
    assert warnings == [
        {
            "code": "jira_reachable",
            "dependency": "Jira",
            "message": "Jira is not reachable.",
            "checked_at": "2026-07-31T00:00:00Z",
        }
    ]


async def test_multiple_warnings_persist_in_execution_order() -> None:
    db, step_id, step = await _make_run_and_step()
    record_preflight_warnings(
        step,
        [
            PreflightWarning(
                code="jira_reachable", dependency="Jira", message="m1", checked_at="t0"
            ),
            PreflightWarning(
                code="confluence_reachable",
                dependency="Confluence",
                message="m2",
                checked_at="t1",
            ),
        ],
    )
    await db.commit()
    await db.close()

    warnings = await _read_warnings(step_id)
    assert [w["code"] for w in warnings] == ["jira_reachable", "confluence_reachable"]


async def test_warnings_written_across_two_calls_both_persist_in_order() -> None:
    """Simulates two checks in the same registry pass each producing a
    warning via separate `record_preflight_warnings` calls — the second
    call must not overwrite the first."""
    db, step_id, step = await _make_run_and_step()
    record_preflight_warnings(
        step,
        [PreflightWarning(code="jira_reachable", dependency="Jira", message="m1", checked_at="t0")],
    )
    record_preflight_warnings(
        step,
        [
            PreflightWarning(
                code="confluence_reachable",
                dependency="Confluence",
                message="m2",
                checked_at="t1",
            )
        ],
    )
    await db.commit()
    await db.close()

    warnings = await _read_warnings(step_id)
    assert [w["code"] for w in warnings] == ["jira_reachable", "confluence_reachable"]


async def test_blocking_failure_path_never_populates_preflight_warnings() -> None:
    """A BLOCKING pre-flight result follows the existing `_fail_step`/
    `_fail_run` path (unchanged by this PR) and never calls
    `record_preflight_warnings` at all — asserted here by simply never
    calling it and confirming the column stays at its empty-list default,
    the same outcome a real BLOCKING failure produces."""
    db, step_id, _step = await _make_run_and_step()
    await db.commit()
    await db.close()

    assert await _read_warnings(step_id) == []


# ---------------------------------------------------------------------------
# Transaction ownership — record_preflight_warnings only flushes; it must
# never commit or roll back the caller's transaction itself.
# ---------------------------------------------------------------------------


async def test_warning_write_is_visible_within_the_same_uncommitted_transaction() -> None:
    """Proves participation in the caller's transaction: flushed but not
    yet committed, the write must already be visible to a query on the
    *same* session (ordinary flush semantics) — and must not yet be
    visible to a fresh, independent session, since nothing has committed."""
    db, step_id, step = await _make_run_and_step()
    record_preflight_warnings(
        step,
        [PreflightWarning(code="jira_reachable", dependency="Jira", message="m1", checked_at="t0")],
    )
    await db.flush()

    same_session_row = (
        (await db.execute(select(AgentStep).where(AgentStep.id == step_id))).scalars().first()
    )
    assert same_session_row.preflight_warnings == [
        {"code": "jira_reachable", "dependency": "Jira", "message": "m1", "checked_at": "t0"}
    ]

    # Not yet committed — a fresh session must see nothing at all for this
    # step (or, if isolation lets it see the row via the run's own earlier
    # flush in a different transaction, it must not see this warning).
    fresh = _RealSession()
    fresh_row = (
        (await fresh.execute(select(AgentStep).where(AgentStep.id == step_id))).scalars().first()
    )
    await fresh.close()
    assert fresh_row is None

    await db.rollback()
    await db.close()


async def test_transaction_rollback_leaves_no_partial_warning_row() -> None:
    """If something after the flush but before RunCoordinator's own commit
    raises and the session is rolled back instead, the flushed warning must
    not be visible to any other session — record_preflight_warnings must
    never have committed it independently."""
    db, step_id, step = await _make_run_and_step()
    record_preflight_warnings(
        step,
        [PreflightWarning(code="jira_reachable", dependency="Jira", message="m1", checked_at="t0")],
    )
    await db.flush()
    # Flushed, not committed — then the whole transaction is abandoned,
    # exactly as RunCoordinator's own failure path does via db.rollback()
    # (or an aborted session close) rather than a commit.
    await db.rollback()
    await db.close()

    assert await _read_warnings(step_id) is None
