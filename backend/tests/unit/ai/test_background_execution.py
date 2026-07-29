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
    background_execution._title_tasks.clear()


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

    monkeypatch.setattr(
        background_execution, "AsyncSessionLocal", lambda: _FakeSession()
    )

    async def fake_generate_title(objective, *, model=None):
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

    monkeypatch.setattr(
        background_execution, "AsyncSessionLocal", lambda: _FakeSession()
    )

    async def fake_generate_title(objective, *, model=None):
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

    async def fake_generate_title(objective, *, model=None):
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr("app.agents.title_generation.generate_title", fake_generate_title)

    # Must not raise, and must not even try to open a session.
    def _fail_if_called():
        raise AssertionError("must not open a session when generate_title itself raised")

    monkeypatch.setattr(background_execution, "AsyncSessionLocal", _fail_if_called)

    from app.models.workflow import Workflow

    await background_execution._generate_title_task(Workflow, uuid.uuid4(), "Some objective", None)


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
