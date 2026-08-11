"""Unit tests for app.orchestrator.background_execution's task bookkeeping.

Pure asyncio-level tests — no real DB, no real agent, no durable queue.
As of KAN-18, `schedule_run_execution` itself enqueues a real
`BackgroundJob` row (real DB access) rather than creating a task directly —
that behavior has its own integration coverage in
tests/integration/test_background_execution_scheduling.py. What stays a
pure unit concern is `_track_current_task`, the primitive both
`_execute_run_task` and `_resume_step_task` call on themselves once a
`Worker` actually starts running one: tracking the current task (so it
isn't garbage-collected mid-run), looking it up by run_id for cancellation,
and cleaning up after completion. Exercised directly here via a bare
`asyncio.create_task` wrapping a coroutine that calls `_track_current_task`
itself — the same shape a claimed job's task has, without needing a
Worker, a queue, or a DB to set one up.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest

from app.orchestrator import background_execution


@pytest.fixture(autouse=True)
def _clean_task_registries():
    """These module-level dicts/sets are process-global — make sure a
    failed assertion in one test can't leak state into the next."""
    yield
    background_execution._running_tasks.clear()
    background_execution._tasks_by_run_id.clear()
    background_execution._title_tasks.clear()


def _spawn_tracked(run_id: uuid.UUID, body) -> asyncio.Task:
    """The shape a claimed job's task actually has: something that calls
    `_track_current_task(run_id)` as its first act, then awaits `body`."""

    async def _wrapped() -> None:
        background_execution._track_current_task(run_id)
        await body()

    return asyncio.create_task(_wrapped())


@pytest.mark.asyncio
async def test_track_current_task_tracks_and_cleans_up():
    started = asyncio.Event()
    release = asyncio.Event()

    async def body():
        started.set()
        await release.wait()

    run_id = uuid.uuid4()
    task = _spawn_tracked(run_id, body)

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
async def test_track_current_task_is_a_noop_outside_a_task():
    """Called synchronously (no enclosing asyncio.Task) — must not raise,
    just have nothing to track. Guards a real call site: `_execute_run_task`
    calls this unconditionally, including in any future/test context that
    might invoke it as a bare coroutine rather than via create_task."""
    background_execution._track_current_task(uuid.uuid4())  # must not raise


@pytest.mark.asyncio
async def test_cancel_run_returns_false_for_unknown_run_id():
    assert background_execution.cancel_run(uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_cancel_run_cancels_tracked_task():
    started = asyncio.Event()

    async def body():
        started.set()
        await asyncio.sleep(60)  # long enough to be reliably still running

    run_id = uuid.uuid4()
    task = _spawn_tracked(run_id, body)
    await asyncio.wait_for(started.wait(), timeout=1)

    assert background_execution.cancel_run(run_id) is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_track_current_task_survives_gc_pressure():
    """Regression guard for the documented asyncio.create_task GC pitfall:
    without a strong reference held somewhere, a task with no other
    referrers can be collected before it finishes. `_track_current_task`
    must keep one itself (_running_tasks) so a caller who doesn't hold the
    Task it spawned is still safe — the same guarantee `schedule_run_
    execution` used to provide directly, now provided by whatever runs the
    claimed job instead."""
    import gc

    release = asyncio.Event()
    ran_to_completion = False

    async def body():
        nonlocal ran_to_completion
        await release.wait()
        ran_to_completion = True

    _spawn_tracked(uuid.uuid4(), body)
    # Don't keep the returned Task around — simulate a caller that only
    # cares about fire-and-forget dispatch.
    gc.collect()
    await asyncio.sleep(0)
    gc.collect()

    release.set()
    await asyncio.sleep(0.05)
    assert ran_to_completion is True


# ---------------------------------------------------------------------------
# Title generation — schedule_title_generation / _generate_title_task
#
# Pure asyncio-level tests, same style as above: patch the module's own
# `_generate_title_task` (or, for the "does it swallow errors" cases,
# `generate_title` itself) rather than touching a real DB. The "does it
# actually update Workflow.title in Postgres" behavior belongs in an
# integration test — see tests/integration/test_background_execution_api.py.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_title_generation_tracks_and_cleans_up_task(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_generate_title_task(*args, **kwargs):
        started.set()
        await release.wait()

    monkeypatch.setattr(background_execution, "_generate_title_task", fake_generate_title_task)

    workflow_id = uuid.uuid4()
    task = background_execution.schedule_title_generation(workflow_id, "Some objective")

    await asyncio.wait_for(started.wait(), timeout=1)
    assert task in background_execution._title_tasks

    release.set()
    await asyncio.wait_for(task, timeout=1)

    await asyncio.sleep(0)
    assert task not in background_execution._title_tasks


@pytest.mark.asyncio
async def test_generate_title_task_persists_generated_title(monkeypatch):
    """The task fetches the workflow by id in its own session and writes
    the real AI title, then commits."""
    from types import SimpleNamespace

    fake_workflow = SimpleNamespace(id=uuid.uuid4(), title="placeholder")

    class _FakeSession:
        async def get(self, model, obj_id):
            return fake_workflow

        async def commit(self):
            self.committed = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(background_execution, "AsyncSessionLocal", lambda: _FakeSession())

    async def fake_generate_title(objective, *, model=None, goal=None):
        assert objective == "Some objective"
        return "A Real Generated Title"

    monkeypatch.setattr("app.agents.title_generation.generate_title", fake_generate_title)

    from app.models.workflow import Workflow

    await background_execution._generate_title_task(
        Workflow, fake_workflow.id, "Some objective", None
    )

    assert fake_workflow.title == "A Real Generated Title"


@pytest.mark.asyncio
async def test_generate_title_task_noop_when_workflow_vanished(monkeypatch):
    """The workflow was deleted before generation finished — must not
    raise, just return quietly (nothing to patch)."""

    class _FakeSession:
        async def get(self, model, obj_id):
            return None

        async def commit(self):
            raise AssertionError("should never commit when there's no row to update")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(background_execution, "AsyncSessionLocal", lambda: _FakeSession())

    async def fake_generate_title(objective, *, model=None, goal=None):
        return "Some Title"

    monkeypatch.setattr("app.agents.title_generation.generate_title", fake_generate_title)

    from app.models.workflow import Workflow

    # Must not raise.
    await background_execution._generate_title_task(Workflow, uuid.uuid4(), "Some objective", None)


@pytest.mark.asyncio
async def test_generate_title_task_swallows_generate_title_exception(monkeypatch):
    """generate_title() itself only raises on a bug in its own fallback
    path — this is defense in depth, and must never escape the detached
    background task."""

    async def fake_generate_title(objective, *, model=None, goal=None):
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr("app.agents.title_generation.generate_title", fake_generate_title)

    # Must not raise, and must not even try to open a session.
    def _fail_if_called():
        raise AssertionError("must not open a session when generate_title itself raised")

    monkeypatch.setattr(background_execution, "AsyncSessionLocal", _fail_if_called)

    from app.models.workflow import Workflow

    await background_execution._generate_title_task(Workflow, uuid.uuid4(), "Some objective", None)


# ---------------------------------------------------------------------------
# P2 — periodic stale-run sweep loop shape (see `fail_stale_running_runs`
# for the actual query/marking logic, covered by its own integration test).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_run_sweep_runs_at_least_once_then_stops_on_the_stop_event(monkeypatch):
    calls = 0

    async def fake_fail_stale_running_runs(db) -> int:  # noqa: ANN001
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(
        background_execution, "fail_stale_running_runs", fake_fail_stale_running_runs
    )

    stop_event = asyncio.Event()

    async def _stop_after_first_iteration():
        # Give the sweep loop's first iteration a chance to run before
        # asking it to stop — the loop checks the event once per cycle.
        await asyncio.sleep(0)
        stop_event.set()

    await asyncio.gather(
        background_execution.run_stale_run_sweep_forever(
            stop_event, interval=timedelta(seconds=60)
        ),
        _stop_after_first_iteration(),
    )

    assert calls >= 1


@pytest.mark.asyncio
async def test_stale_run_sweep_swallows_a_failed_iteration_and_keeps_looping(monkeypatch):
    calls = 0

    async def flaky_fail_stale_running_runs(db) -> int:  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db hiccup")
        return 0

    monkeypatch.setattr(
        background_execution, "fail_stale_running_runs", flaky_fail_stale_running_runs
    )

    stop_event = asyncio.Event()

    async def _stop_after_two_iterations():
        while calls < 2:
            await asyncio.sleep(0)
        stop_event.set()

    # A near-zero interval so the second iteration fires immediately after
    # the first one's exception is swallowed, without a real 5-minute wait.
    await asyncio.gather(
        background_execution.run_stale_run_sweep_forever(
            stop_event, interval=timedelta(milliseconds=1)
        ),
        _stop_after_two_iterations(),
    )

    assert calls >= 2
