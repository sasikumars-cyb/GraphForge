"""Unit tests for app.orchestrator.background_execution's task bookkeeping.

Pure asyncio-level tests — no real DB, no real agent. Patches the module's
internal `_execute_run_task` with a fast fake coroutine so these tests
verify only what this module is actually responsible for: tracking tasks
(so they aren't garbage-collected mid-run), looking them up by run_id for
cancellation, and cleaning up after completion. The real execution body
(_execute_run_task itself, which opens its own session and calls
RunCoordinator.execute_run) is exercised by the integration tests in
tests/integration/test_background_execution_api.py instead, since it
needs a real DB.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.orchestrator import background_execution


@pytest.fixture(autouse=True)
def _clean_task_registries():
    """These module-level dicts/sets are process-global — make sure a
    failed assertion in one test can't leak state into the next."""
    yield
    background_execution._running_tasks.clear()
    background_execution._tasks_by_run_id.clear()


@pytest.mark.asyncio
async def test_schedule_run_execution_tracks_and_cleans_up_task(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_execute_run_task(*args, **kwargs):
        started.set()
        await release.wait()

    monkeypatch.setattr(background_execution, "_execute_run_task", fake_execute_run_task)

    run_id = uuid.uuid4()
    task = background_execution.schedule_run_execution(
        run_id=run_id,
        subject=None,
        goal="fake_goal",
        model=None,
        extras=None,
        agent_id="fake_agent",
        registry=None,
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    assert task in background_execution._running_tasks
    assert background_execution._tasks_by_run_id[run_id] is task

    release.set()
    await asyncio.wait_for(task, timeout=1)

    # Cleanup runs via add_done_callback — give the event loop one tick.
    await asyncio.sleep(0)
    assert task not in background_execution._running_tasks
    assert run_id not in background_execution._tasks_by_run_id


@pytest.mark.asyncio
async def test_cancel_run_returns_false_for_unknown_run_id():
    assert background_execution.cancel_run(uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_cancel_run_cancels_tracked_task(monkeypatch):
    started = asyncio.Event()

    async def fake_execute_run_task(*args, **kwargs):
        started.set()
        await asyncio.sleep(60)  # long enough to be reliably still running

    monkeypatch.setattr(background_execution, "_execute_run_task", fake_execute_run_task)

    run_id = uuid.uuid4()
    task = background_execution.schedule_run_execution(
        run_id=run_id,
        subject=None,
        goal="fake_goal",
        model=None,
        extras=None,
        agent_id="fake_agent",
        registry=None,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    assert background_execution.cancel_run(run_id) is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_schedule_run_execution_survives_gc_pressure(monkeypatch):
    """Regression guard for the documented asyncio.create_task GC pitfall:
    without holding a strong reference, a task with no other referrers can
    be collected before it finishes. schedule_run_execution must keep one
    itself (_running_tasks) so callers who don't hold the returned Task
    are still safe."""
    import gc

    release = asyncio.Event()
    ran_to_completion = False

    async def fake_execute_run_task(*args, **kwargs):
        nonlocal ran_to_completion
        await release.wait()
        ran_to_completion = True

    monkeypatch.setattr(background_execution, "_execute_run_task", fake_execute_run_task)

    background_execution.schedule_run_execution(
        run_id=uuid.uuid4(),
        subject=None,
        goal="fake_goal",
        model=None,
        extras=None,
        agent_id="fake_agent",
        registry=None,
    )
    # Don't keep the returned Task around — simulate a caller that only
    # cares about fire-and-forget dispatch.
    gc.collect()
    await asyncio.sleep(0)
    gc.collect()

    release.set()
    await asyncio.sleep(0.05)
    assert ran_to_completion is True
