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
from datetime import UTC, datetime
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
    extras: dict[str, Any] | None,
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

            coordinator = RunCoordinator(db=db, registry=registry, selector=None)
            try:
                # `on_complete` is passed as execute_run's on_pre_commit, not
                # called after it returns: both used to be two separate
                # commits (run status, then this callback's own bookkeeping
                # — e.g. advancing a Workflow's current_stage), which let a
                # fast poller observe a stage as "completed" before the
                # workflow it belongs to had advanced. Passing it through
                # merges both into execute_run's single commit — see that
                # method's docstring for the atomicity guarantee and the
                # fallback if the hook itself fails.
                await coordinator.execute_run(
                    run, agent_id, agent, subject, goal, model, extras, on_pre_commit=on_complete
                )
            except Exception:
                # execute_run already persisted status="failed" and
                # committed on any failure path before re-raising — nothing
                # further to persist here, just stop the exception from
                # propagating out of a detached task (which asyncio would
                # otherwise only surface as an unhandled "exception never
                # retrieved" warning on GC).
                logger.exception("background_run_failed run_id=%s agent_id=%s", run_id, agent_id)
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
    extras: dict[str, Any] | None,
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
    """Mark any Run left at status="running" or "queued" as failed.

    Call once at process startup (see app.main's lifespan). asyncio.create_
    task-based background execution (schedule_run_execution) does not
    survive a process restart — anything a previous process left at
    "running" is guaranteed orphaned, not actually still in progress, and
    would otherwise sit there forever with no task left to finish it.

    "queued" is orphaned by the exact same mechanism and was missing here:
    create_pending_run commits the row at status="queued" in the request's
    own session *before* schedule_run_execution ever creates the asyncio
    task that would advance it to "running" (see run_coordinator.py). A
    restart landing in that gap — e.g. the dev server's --reload firing
    while a request is in flight — leaves the row at "queued" forever:
    never "running", so this function's WHERE clause skipped it, and no
    task survives to pick it up either. Found via 7 real rows stuck this
    way (started_at/completed_at both null, no error_message) after a
    reload during active backend editing.

    Deliberately does not touch Workflow.status: "in_progress" is a
    workflow's normal steady state between stages, not a signal that a
    stage was actively executing when the process stopped — only a Run's
    own "running"/"queued" status means that. Leaving the workflow as-is
    lets the user retry the failed stage via POST /workflows/{id}/continue.

    Returns the number of runs recovered (for logging/observability).
    """
    result = await db.execute(select(Run).where(Run.status.in_(("running", "queued"))))
    orphaned = list(result.scalars().all())
    for run in orphaned:
        run.status = "failed"
        run.error_message = "Interrupted by server restart."
        run.completed_at = datetime.now(UTC)
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


# ---------------------------------------------------------------------------
# Title generation — same fire-and-forget shape as run execution above, for
# a much smaller job: one LLM call that only ever patches one column.
# ---------------------------------------------------------------------------
#
# Both `workflow_service.create_workflow()` and `POST /agent-runs`
# (app.api.v1.routers.agent_runs) used to `await generate_title(...)`
# before returning, so every workflow/run creation paid a full LLM
# round-trip in its response latency for a purely cosmetic field — neither
# response schema returns `title` synchronously in a way that requires the
# *generated* value (ContinueWorkflowResponse never returns it at all;
# AgentRunResponse's `title` is populated from whatever is committed by the
# time the response is built, and now that's the placeholder). Both models
# (`Workflow`, `Run`) have an identical `id`/`title` shape, so one generic
# task body serves both — parameterized by which model class to patch,
# not two near-duplicate task functions.
#
# The row is created immediately with the same deterministic fallback
# `generate_title` already used on failure, and the real title (if
# generation succeeds) lands in a follow-up UPDATE once the background
# task finishes — the title is never worse than today's failure-path
# fallback, and callers that read it afterward simply see whichever value
# is currently committed.

_title_tasks: set[Any] = set()


async def _generate_title_task(
    model_cls: type[Any], row_id: uuid.UUID, objective: str, model: str | None
) -> None:
    from app.agents.title_generation import generate_title

    try:
        title = await generate_title(objective, model=model)
    except Exception:
        # generate_title() itself only raises on a bug in its own fallback
        # path (it catches AppError internally) — this is defense in depth,
        # matching _execute_run_task's "never let an exception escape a
        # detached task" shape.
        logger.exception(
            "background_title_generation_failed model=%s id=%s", model_cls.__name__, row_id
        )
        return

    async with AsyncSessionLocal() as db:
        try:
            row = await db.get(model_cls, row_id)
            if row is None:
                # Row was deleted before title generation finished —
                # nothing to patch, not an error.
                return
            row.title = title
            await db.commit()
        except Exception:
            logger.exception(
                "background_title_persist_failed model=%s id=%s", model_cls.__name__, row_id
            )


def schedule_title_generation(
    row_id: uuid.UUID,
    objective: str,
    model: str | None = None,
    *,
    model_cls: type[Any] | None = None,
) -> Any:
    """Fire-and-forget the real AI title generation for a just-created
    Workflow (default) or Run (`model_cls=Run`). Returns the created
    asyncio.Task (mainly useful for tests)."""
    import asyncio

    from app.models.workflow import Workflow

    task = asyncio.create_task(
        _generate_title_task(model_cls or Workflow, row_id, objective, model)
    )
    _title_tasks.add(task)
    task.add_done_callback(_title_tasks.discard)
    return task
