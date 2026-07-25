"""Backgrounds Run execution off the HTTP request lifecycle.

Today (see run_coordinator.py's module docstring) an agent run's actual
work — RunCoordinator.execute_run() — was awaited synchronously inside the
FastAPI request handler that created it. With no request-timeout
middleware and no cancellation shielding anywhere in the app, a dropped
client connection (browser navigation, tab close, refresh) can cancel the
in-flight ASGI request task and take the agent's work down with it, before
the run ever reaches a terminal, committed status.

This module decouples the two: a router creates the Run row (status via
RunCoordinator.create_pending_run) and commits it in its own request-scoped
session, then hands the rest of the work to `schedule_run_execution`, which
runs it via `asyncio.create_task` on the same event loop, using its own
independent DB session (mirroring the only other background job in this
codebase, app/indexer/workers/index_worker.py — same "open a fresh
AsyncSessionLocal, re-fetch rows by id, never let an exception escape
uncaught" shape, adapted from BackgroundTasks to asyncio.create_task since
we need the work to survive the response actually being sent, not just
follow it).

Because this all runs in-process on the same event loop (not a real task
queue), the run's inputs (Subject, goal, extras, the resolved agent_id) are
passed straight through as plain Python objects — no serialization needed.
The trade-off (documented in the architecture plan) is that this does not
survive a process restart or scale across multiple worker processes; see
app.main's startup lifespan hook for the orphaned-run recovery that covers
the restart case.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Subject
from app.database.session import AsyncSessionLocal
from app.models.run import Run
from app.orchestrator.registry import AgentRegistry
from app.orchestrator.run_coordinator import RunCoordinator

logger = logging.getLogger(__name__)

# Strong references to in-flight tasks: asyncio.create_task() only holds a
# weak reference to the returned Task internally, so without this a task
# can be garbage-collected mid-run. See:
# https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
_running_tasks: set[Any] = set()

# run_id -> Task, so a run can be looked up and cancelled by id.
_tasks_by_run_id: dict[uuid.UUID, Any] = {}

# Called after a run reaches a terminal state, with (db, run), so callers
# (e.g. workflows) can run post-execution bookkeeping against the same
# background session before it closes. Exceptions raised here are logged,
# not re-raised — the run's own status is already committed by this point.
OnComplete = Callable[[AsyncSession, Run], Awaitable[None]]


async def _execute_run_task(
    run_id: uuid.UUID,
    subject: Subject,
    goal: str,
    model: str | None,
    extras: dict | None,
    agent_id: str,
    registry: AgentRegistry,
    on_complete: OnComplete | None,
) -> None:
    async with AsyncSessionLocal() as db:
        try:
            run = await db.get(Run, run_id)
            if run is None:
                logger.error("background_run_vanished run_id=%s", run_id)
                return

            entry = registry.get(agent_id)
            if entry is None:
                # Shouldn't happen — the agent was already resolved once by
                # create_pending_run in the caller's session — but the
                # registry is a static, process-wide singleton, so this can
                # only mean a genuine bug, not a race.
                run.status = "failed"
                run.error_message = f"Agent '{agent_id}' vanished from the registry."
                await db.commit()
                return
            _manifest, agent = entry

            coordinator = RunCoordinator(db=db, registry=registry, selector=None)  # type: ignore[arg-type]
            try:
                await coordinator.execute_run(run, agent_id, agent, subject, goal, model, extras)
            except Exception:
                # execute_run already persisted status="failed" and
                # committed on any failure path before re-raising — nothing
                # further to persist here, just stop the exception from
                # propagating out of a detached task (which asyncio would
                # otherwise only surface as an unhandled "exception never
                # retrieved" warning on GC).
                logger.exception("background_run_failed run_id=%s agent_id=%s", run_id, agent_id)

            if on_complete is not None:
                try:
                    await on_complete(db, run)
                except Exception:
                    logger.exception(
                        "background_run_on_complete_failed run_id=%s agent_id=%s", run_id, agent_id
                    )
        except Exception:
            # Defense in depth beyond execute_run's own handling — e.g. a
            # session-level failure (couldn't open a connection, run
            # vanished mid-flight). Best-effort mark the run failed so it
            # doesn't sit at "running" forever; the startup recovery in
            # app.main covers anything this can't reach (e.g. db is down).
            logger.exception("background_run_task_error run_id=%s", run_id)
            try:
                run = await db.get(Run, run_id)
                if run is not None and run.status not in ("completed", "failed"):
                    run.status = "failed"
                    run.error_message = "Execution failed unexpectedly."
                    await db.commit()
            except Exception:
                logger.exception("background_run_failure_persist_failed run_id=%s", run_id)


def schedule_run_execution(
    run_id: uuid.UUID,
    subject: Subject,
    goal: str,
    model: str | None,
    extras: dict | None,
    agent_id: str,
    registry: AgentRegistry,
    on_complete: OnComplete | None = None,
) -> Any:
    """Fire-and-forget a run's execution, decoupled from the calling
    request. Returns the created asyncio.Task (mainly useful for tests)."""
    import asyncio

    task = asyncio.create_task(
        _execute_run_task(run_id, subject, goal, model, extras, agent_id, registry, on_complete)
    )
    _running_tasks.add(task)
    _tasks_by_run_id[run_id] = task

    def _cleanup(t: Any) -> None:
        _running_tasks.discard(t)
        if _tasks_by_run_id.get(run_id) is t:
            del _tasks_by_run_id[run_id]

    task.add_done_callback(_cleanup)
    return task


async def recover_orphaned_runs(db: AsyncSession) -> int:
    """Mark any Run left at status="running" as failed.

    Call once at process startup (see app.main's lifespan). asyncio.create_
    task-based background execution (schedule_run_execution) does not
    survive a process restart — anything a previous process left at
    "running" is guaranteed orphaned, not actually still in progress, and
    would otherwise sit there forever with no task left to finish it.

    Deliberately does not touch Workflow.status: "in_progress" is a
    workflow's normal steady state between stages, not a signal that a
    stage was actively executing when the process stopped — only a Run's
    own "running" status means that. Leaving the workflow as-is lets the
    user retry the failed stage via POST /workflows/{id}/continue.

    Returns the number of runs recovered (for logging/observability).
    """
    result = await db.execute(select(Run).where(Run.status == "running"))
    orphaned = list(result.scalars().all())
    for run in orphaned:
        run.status = "failed"
        run.error_message = "Interrupted by server restart."
        run.completed_at = datetime.now(timezone.utc)
    if orphaned:
        await db.commit()
        logger.warning(
            "recovered_orphaned_runs count=%d run_ids=%s",
            len(orphaned),
            [str(r.id) for r in orphaned],
        )
    return len(orphaned)


def cancel_run(run_id: uuid.UUID) -> bool:
    """Request cancellation of a run's in-flight background task.

    Best-effort: asyncio.Task.cancel() raises CancelledError at the task's
    next await point, it doesn't stop it instantly. Returns True if a task
    was found and cancellation was requested, False if the run has no
    tracked in-flight task (already finished, or never backgrounded).
    """
    task = _tasks_by_run_id.get(run_id)
    if task is None:
        return False
    task.cancel()
    return True
