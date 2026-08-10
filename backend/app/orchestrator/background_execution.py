"""Backgrounds Run execution off the HTTP request lifecycle.

Today (see run_coordinator.py's module docstring) an agent run's actual
work — RunCoordinator.execute_run() — was awaited synchronously inside the
FastAPI request handler that created it. With no request-timeout
middleware and no cancellation shielding anywhere in the app, a dropped
client connection (browser navigation, tab close, refresh) can cancel the
in-flight ASGI request task and take the agent's work down with it, before
the run ever reaches a terminal, committed status.

This module decouples the two — but as of KAN-18, not via a bare
`asyncio.create_task()` at schedule time. `schedule_run_execution`/
`schedule_resume_execution` now enqueue a durable `BackgroundJob` row (see
`app.orchestrator.job_queue`), committed on its own right after the
caller's own Run-row commit — not merged into one transaction with it (every
call site already commits the Run row first; see `JobQueue.enqueue`'s
docstring for why that structure was kept rather than reordered). That
leaves a small, non-zero window between the two commits, and is still a
strict improvement over what it replaces: an in-memory
`asyncio.create_task()`, durable for exactly as long as the process stays
up. A `Worker` (`app.orchestrator.worker`, started from `app.main`'s
lifespan) claims the row and only then calls `asyncio.create_task` to
actually run `_execute_run_task`/`_resume_step_task` — the same two
functions this module has always had, unchanged in what they do, just no
longer invoked directly by the router.

Because the queue is Postgres, not memory, the run's inputs (Subject,
extras, an `OnCompleteSpec`) must be JSON-safe to cross it — see
`_serialize_extras`/`_deserialize_extras` below for the one wrinkle that
creates: `extras` has always carried live ORM objects (`Workflow`,
`_StandalonePlanningContext`) in the four workflow-router call sites, which
cannot survive a trip through JSON. Those get swapped for an id-based
reference at enqueue time and re-fetched fresh, in the worker's own
session, at execution time — the same "re-fetch by id in a new session"
discipline `_execute_run_task` already applies to the Run row itself,
extended to cover what `extras` carries too.

A crash *while a job is claimed* (not just before it's ever picked up) is
covered by the job's lease: `JobQueue.reclaim_expired_leases` (called from
`app.main`'s lifespan, alongside `recover_orphaned_runs` below) requeues
any job whose lease expired without completing, so any process that comes
back up — this one restarted, or a separate worker — can retry it. No
heartbeat exists yet (see worker.py's module docstring for what that
trade-off means); every handler here is written to be safe to re-run from
scratch on the same Run/AgentStep row.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Subject
from app.database.session import AsyncSessionLocal
from app.models.background_job import BackgroundJob
from app.models.run import Run
from app.models.workflow import Workflow
from app.orchestrator.job_queue import JobQueue
from app.orchestrator.on_complete_spec import OnCompleteSpec
from app.orchestrator.on_complete_spec import resolve as resolve_on_complete_spec
from app.orchestrator.registry import AgentRegistry, global_registry
from app.orchestrator.run_coordinator import RunCoordinator
from app.orchestrator.worker import register_handler

logger = logging.getLogger(__name__)

JOB_TYPE_RUN_EXECUTION = "run_execution"
JOB_TYPE_RESUME_EXECUTION = "resume_execution"

# Strong references to in-flight tasks: asyncio.create_task() only holds a
# weak reference to the returned Task internally, so without this a task
# can be garbage-collected mid-run. See:
# https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
_running_tasks: set[Any] = set()

# run_id -> Task, so a run can be looked up and cancelled by id. Populated
# by the handler functions themselves (via asyncio.current_task()) rather
# than by whatever created the task, since that's now Worker.run_forever —
# a generic dispatcher with no notion of "run_id" — not this module.
_tasks_by_run_id: dict[uuid.UUID, Any] = {}

# Called after a run reaches a terminal state, with (db, run), so callers
# (e.g. workflows) can run post-execution bookkeeping against the same
# background session before it closes. Exceptions raised here are logged,
# not re-raised — the run's own status is already committed by this point.
OnComplete = Callable[[AsyncSession, Run], Awaitable[None]]


def _track_current_task(run_id: uuid.UUID) -> None:
    """Register the task this coroutine is running inside (if any) so
    `cancel_run`/`_running_tasks` keep working exactly as before — the
    task's identity moved (it's now created by `Worker`, not by this
    module), but the tracking contract callers depend on has not."""
    import asyncio

    task = asyncio.current_task()
    if task is None:
        return
    _running_tasks.add(task)
    _tasks_by_run_id[run_id] = task

    def _cleanup(t: Any) -> None:
        _running_tasks.discard(t)
        if _tasks_by_run_id.get(run_id) is t:
            del _tasks_by_run_id[run_id]

    task.add_done_callback(_cleanup)


# ---------------------------------------------------------------------------
# extras serialization — the one place a live ORM object crossing the queue
# boundary is handled explicitly, rather than generically.
# ---------------------------------------------------------------------------
#
# Verified by reading every call site (app.api.v1.routers.agent_runs,
# app.api.v1.routers.workflows) rather than assumed: `extras` only ever
# carries a `Workflow` under "workflow"/"source_workflow"/"parent_workflow",
# or a `_StandalonePlanningContext` under "workflow" (agent_runs.py's
# planning_run_id path) — nothing else in `extras` is ever more than a
# JSON-safe primitive (a UUID, a plain dict, a list of strings) already
# handled by `fastapi.encoders.jsonable_encoder` at `JobQueue.enqueue`'s own
# boundary. Adding a third shape means adding one more arm below,
# deliberately — same discipline as `OnCompleteSpec`.

_WORKFLOW_LIKE_KEYS = frozenset({"workflow", "source_workflow", "parent_workflow"})


def _serialize_extras(extras: dict[str, Any] | None) -> dict[str, Any] | None:
    if extras is None:
        return None

    from app.api.v1.routers.agent_runs import _StandalonePlanningContext

    serialized: dict[str, Any] = {}
    for key, value in extras.items():
        if key in _WORKFLOW_LIKE_KEYS and isinstance(value, Workflow):
            serialized[key] = {"__ref__": "workflow", "id": str(value.id)}
        elif isinstance(value, _StandalonePlanningContext):
            # `.runs[0]` is `_StandalonePlanningRun`, which proxies `.id`
            # straight through to the wrapped, already-loaded `Run` — see
            # that class's own docstring.
            serialized[key] = {
                "__ref__": "standalone_planning_context",
                "planning_run_id": str(value.runs[0].id),
            }
        else:
            serialized[key] = value
    return serialized


async def _deserialize_extras(
    db: AsyncSession, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    if payload is None:
        return None

    from sqlalchemy.orm import selectinload

    from app.api.v1.routers.agent_runs import _StandalonePlanningContext
    from app.services import workflow_service

    deserialized: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict) and value.get("__ref__") == "workflow":
            deserialized[key] = await workflow_service.get_workflow(db, uuid.UUID(value["id"]))
        elif isinstance(value, dict) and value.get("__ref__") == "standalone_planning_context":
            # `_StandalonePlanningContext`/`_StandalonePlanningRun` proxy
            # `.steps` straight through to the wrapped Run — a plain
            # `db.get(Run, id)` leaves that relationship unloaded, and a
            # bare (synchronous-looking) attribute access on an unloaded
            # relationship under SQLAlchemy's async ORM raises
            # MissingGreenlet rather than lazy-loading transparently.
            # `selectinload` here is the same eager-loading discipline
            # `workflow_service.get_workflow` already applies to
            # `Workflow.runs`/`Run.steps` two lines above.
            planning_run = (
                await db.execute(
                    select(Run)
                    .where(Run.id == uuid.UUID(value["planning_run_id"]))
                    .options(selectinload(Run.steps))
                )
            ).scalar_one_or_none()
            deserialized[key] = _StandalonePlanningContext(planning_run)  # type: ignore[arg-type]
        else:
            deserialized[key] = value
    return deserialized


async def _execute_run_task(
    run_id: uuid.UUID,
    subject: Subject,
    goal: str,
    model: str | None,
    extras_payload: dict[str, Any] | None,
    agent_id: str,
    registry: AgentRegistry,
    on_complete: OnComplete | None,
) -> None:
    """`extras_payload` is the *serialized* form (see `_serialize_extras`) —
    deserialized here, inside this function's own session, rather than by
    the caller in a separate one. That distinction is load-bearing, not
    stylistic: `_deserialize_extras` re-fetches live ORM objects
    (`Workflow`, a wrapped `Run`), and an object fetched in one session and
    then handed to code running under a *different*, already-closed
    session raises `DetachedInstanceError` the moment anything lazy-loads
    a relationship on it — exactly the bug a separate deserialization
    session produced here before this was fixed.
    """
    _track_current_task(run_id)
    async with AsyncSessionLocal() as db:
        try:
            run = await db.get(Run, run_id)
            if run is None:
                logger.error("background_run_vanished run_id=%s", run_id)
                return

            extras = await _deserialize_extras(db, extras_payload)

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


async def _run_execution_handler(payload: dict[str, Any]) -> None:
    """The `JOB_TYPE_RUN_EXECUTION` handler — deserializes a claimed job's
    payload and calls `_execute_run_task`, exactly what a direct
    `asyncio.create_task()` call used to invoke directly. Always uses
    `global_registry`: every current call site already passes that same
    process-wide singleton (verified by reading each one), and a registry
    is process-local infrastructure, not job data — there is nothing
    meaningful to serialize for it. Passes `extras` through to
    `_execute_run_task` still serialized — see that function's own
    docstring for why deserializing it here, in a separate session, would
    be a bug rather than a simplification."""
    on_complete = resolve_on_complete_spec(
        OnCompleteSpec.from_payload(payload.get("on_complete_spec"))
    )

    await _execute_run_task(
        run_id=uuid.UUID(payload["run_id"]),
        subject=Subject.model_validate(payload["subject"]),
        goal=payload["goal"],
        model=payload.get("model"),
        extras_payload=payload.get("extras"),
        agent_id=payload["agent_id"],
        registry=global_registry,
        on_complete=on_complete,
    )


async def schedule_run_execution(
    db: AsyncSession,
    run_id: uuid.UUID,
    subject: Subject,
    goal: str,
    model: str | None,
    extras: dict[str, Any] | None,
    agent_id: str,
    registry: AgentRegistry,
    on_complete_spec: OnCompleteSpec | None = None,
) -> uuid.UUID:
    """Durably enqueue a run's execution, decoupled from the calling
    request. Commits on its own (see `JobQueue.enqueue`'s docstring) —
    call this any time after the Run row itself is committed, matching
    every current call site's existing structure. Returns the enqueued
    `BackgroundJob`'s id.

    `registry` is kept as a required, explicit parameter for call-site
    clarity even though the queued execution path always dispatches
    through `global_registry` — every caller already passes that same
    singleton today, so this is not a behavior change, only documentation
    of what actually runs.
    """
    del registry  # see docstring — always global_registry once claimed
    job = await JobQueue(db).enqueue(
        JOB_TYPE_RUN_EXECUTION,
        {
            "run_id": str(run_id),
            "subject": subject,
            "goal": goal,
            "model": model,
            "extras": _serialize_extras(extras),
            "agent_id": agent_id,
            "on_complete_spec": on_complete_spec.to_payload() if on_complete_spec else None,
        },
        correlation_id=str(run_id),
    )
    return job.id


async def _resume_step_task(
    run_id: uuid.UUID,
    step_id: uuid.UUID,
    subject: Subject,
    goal: str,
    model: str | None,
    extras_payload: dict[str, Any] | None,
    agent_id: str,
    registry: AgentRegistry,
    on_complete: OnComplete | None,
) -> None:
    """Mirrors `_execute_run_task`, but resumes an existing paused
    `AgentStep` (`RunCoordinator.resume_step`) instead of creating a new
    one — how `POST /workflows/{id}/clarify` answers get applied off the
    request lifecycle, for the same reasons `_execute_run_task` is.
    `extras_payload` is serialized — see `_execute_run_task`'s docstring
    for why it is deserialized here, inside this function's own session,
    rather than by the caller in a separate one."""
    from app.models.agent_step import AgentStep

    _track_current_task(run_id)
    async with AsyncSessionLocal() as db:
        try:
            run = await db.get(Run, run_id)
            step = await db.get(AgentStep, step_id)
            if run is None or step is None:
                logger.error("background_resume_vanished run_id=%s step_id=%s", run_id, step_id)
                return

            extras = await _deserialize_extras(db, extras_payload)

            entry = registry.get(agent_id)
            if entry is None:
                run.status = "failed"
                run.error_message = f"Agent '{agent_id}' vanished from the registry."
                step.status = "failed"
                await db.commit()
                return
            _manifest, agent = entry

            coordinator = RunCoordinator(db=db, registry=registry, selector=None)
            try:
                await coordinator.resume_step(
                    run,
                    step,
                    agent_id,
                    agent,
                    subject,
                    goal,
                    model,
                    extras,
                    on_pre_commit=on_complete,
                )
            except Exception:
                logger.exception("background_resume_failed run_id=%s agent_id=%s", run_id, agent_id)
        except Exception:
            logger.exception("background_resume_task_error run_id=%s", run_id)
            try:
                run = await db.get(Run, run_id)
                if run is not None and run.status not in ("completed", "failed"):
                    run.status = "failed"
                    run.error_message = "Execution failed unexpectedly."
                    await db.commit()
            except Exception:
                logger.exception("background_resume_failure_persist_failed run_id=%s", run_id)


async def _resume_execution_handler(payload: dict[str, Any]) -> None:
    """The `JOB_TYPE_RESUME_EXECUTION` handler — mirrors
    `_run_execution_handler` for `_resume_step_task`."""
    on_complete = resolve_on_complete_spec(
        OnCompleteSpec.from_payload(payload.get("on_complete_spec"))
    )

    await _resume_step_task(
        run_id=uuid.UUID(payload["run_id"]),
        step_id=uuid.UUID(payload["step_id"]),
        subject=Subject.model_validate(payload["subject"]),
        goal=payload["goal"],
        model=payload.get("model"),
        extras_payload=payload.get("extras"),
        agent_id=payload["agent_id"],
        registry=global_registry,
        on_complete=on_complete,
    )


async def schedule_resume_execution(
    db: AsyncSession,
    run_id: uuid.UUID,
    step_id: uuid.UUID,
    subject: Subject,
    goal: str,
    model: str | None,
    extras: dict[str, Any] | None,
    agent_id: str,
    registry: AgentRegistry,
    on_complete_spec: OnCompleteSpec | None = None,
) -> uuid.UUID:
    """Durably enqueue a paused step's resumption — same shape and same
    commit contract as `schedule_run_execution`. Returns the enqueued
    `BackgroundJob`'s id."""
    del registry  # see schedule_run_execution's docstring
    job = await JobQueue(db).enqueue(
        JOB_TYPE_RESUME_EXECUTION,
        {
            "run_id": str(run_id),
            "step_id": str(step_id),
            "subject": subject,
            "goal": goal,
            "model": model,
            "extras": _serialize_extras(extras),
            "agent_id": agent_id,
            "on_complete_spec": on_complete_spec.to_payload() if on_complete_spec else None,
        },
        correlation_id=str(run_id),
    )
    return job.id


register_handler(JOB_TYPE_RUN_EXECUTION, _run_execution_handler)
register_handler(JOB_TYPE_RESUME_EXECUTION, _resume_execution_handler)


async def recover_orphaned_runs(db: AsyncSession) -> int:
    """Mark truly-orphaned Runs — "running"/"queued" rows with no
    `BackgroundJob` left that could still retry them — as failed.

    Call once at process startup (see app.main's lifespan), alongside
    `app.orchestrator.worker.reclaim_expired_leases_once` — that function
    is what makes a job survivable across a restart in the first place
    (requeuing anything a dead worker's lease expired on); this function is
    the backstop for what even that can't reach: a Run created before this
    migration (no BackgroundJob row exists for it at all), or one whose
    job was ultimately dead-lettered after exhausting every retry.

    A Run whose job is still `queued`/`leased` is deliberately left alone
    here — the job queue itself will retry it once a worker claims it
    (possibly this very process, moments after this function returns); to
    mark it failed anyway would race the retry it's about to get and undo
    the durability this migration exists to add.

    "queued" is included in the same query for the same historical reason
    it always was: `create_pending_run` commits a Run at status="queued"
    before `schedule_run_execution` ever enqueues the job that would
    advance it — a restart landing in that exact gap leaves a Run with no
    job at all to find.

    Deliberately does not touch Workflow.status: "in_progress" is a
    workflow's normal steady state between stages, not a signal that a
    stage was actively executing when the process stopped — only a Run's
    own "running"/"queued" status means that. Leaving the workflow as-is
    lets the user retry the failed stage via POST /workflows/{id}/continue.

    Returns the number of runs recovered (for logging/observability).
    """
    result = await db.execute(select(Run).where(Run.status.in_(("running", "queued"))))
    candidates = list(result.scalars().all())
    if not candidates:
        return 0

    jobs_result = await db.execute(
        select(BackgroundJob).where(
            BackgroundJob.correlation_id.in_([str(r.id) for r in candidates]),
            BackgroundJob.job_type.in_((JOB_TYPE_RUN_EXECUTION, JOB_TYPE_RESUME_EXECUTION)),
        )
    )
    # A Run may have more than one job row across retries/resumptions —
    # only the most recent one determines whether it's still retryable.
    latest_job_by_run_id: dict[str, BackgroundJob] = {}
    for job in jobs_result.scalars().all():
        existing = latest_job_by_run_id.get(job.correlation_id or "")
        if existing is None or job.created_at > existing.created_at:
            latest_job_by_run_id[job.correlation_id or ""] = job

    orphaned = []
    for run in candidates:
        latest_job = latest_job_by_run_id.get(str(run.id))
        if latest_job is not None and latest_job.status in ("queued", "leased"):
            continue  # the job queue will still retry this one
        run.status = "failed"
        run.error_message = "Interrupted by server restart."
        run.completed_at = datetime.now(UTC)
        orphaned.append(run)

    if orphaned:
        await db.commit()
        logger.warning(
            "recovered_orphaned_runs count=%d run_ids=%s",
            len(orphaned),
            [str(r.id) for r in orphaned],
        )
    return len(orphaned)


# P2 — orphan-run detection for a process that never restarted at all: the
# specific real-incident shape `recover_orphaned_runs` above can't reach.
# That function only catches a Run whose *job* is no longer queued/leased —
# it trusts a "leased" job to mean the run is still genuinely progressing.
# But a job stays "leased" for as long as the worker process that claimed
# it is alive, even if the `asyncio.create_task()` running the actual
# agent work inside it has already died silently (an exception that
# escaped every handler in `RunCoordinator`/`_execute_run_task` without
# ever reaching a terminal Run status — see `RunCoordinator._commit_or_
# fail`'s own docstring for the specific bug this was found from, now
# fixed there; this sweep is the generic backstop for the *next* one).
#
# A time threshold, not job-lease state, is what actually catches that:
# Context Discovery's own budgets (MAX_CYCLES, the mid-loop/final
# synthesis call caps) mean a genuine run essentially never takes this
# long in practice, so a generous ceiling stays a safe, conservative
# signal rather than a false-positive risk against a merely slow run.
STALE_RUN_THRESHOLD = timedelta(minutes=20)


async def fail_stale_running_runs(
    db: AsyncSession, *, older_than: timedelta = STALE_RUN_THRESHOLD
) -> int:
    """Mark any Run stuck at status="running" for longer than `older_than`
    as failed — the truthful state a user-facing "This appears to have
    stalled" reads from, instead of an indefinite spinner. Never touches
    "queued" (that's `recover_orphaned_runs`'s and the job queue's own
    lease-expiry's job, both keyed on real job state, not wall-clock
    time) or "awaiting_input" (a human genuinely may take longer than
    `older_than` to answer a clarification question — that is not
    staleness). Returns the number of runs failed, for logging/
    observability, exactly like `recover_orphaned_runs`.
    """
    cutoff = datetime.now(UTC) - older_than
    result = await db.execute(select(Run).where(Run.status == "running", Run.started_at < cutoff))
    stale = list(result.scalars().all())
    if not stale:
        return 0

    for run in stale:
        run.status = "failed"
        run.error_message = (
            f"This investigation appears to have stalled (no update in over "
            f"{int(older_than.total_seconds() // 60)} minutes) and was automatically marked as "
            "failed. Please try again."
        )
        run.completed_at = datetime.now(UTC)

    await db.commit()
    logger.warning(
        "failed_stale_running_runs count=%d run_ids=%s",
        len(stale),
        [str(r.id) for r in stale],
    )
    return len(stale)


async def run_stale_run_sweep_forever(
    stop_event: asyncio.Event, *, interval: timedelta = timedelta(minutes=5)
) -> None:
    """The periodic counterpart to `recover_orphaned_runs` (startup-only) —
    started as its own background task from `app.main`'s lifespan,
    mirroring `Worker.run_forever`'s own shape exactly (a stop event, a
    fresh session per iteration, exceptions logged and swallowed so one
    bad sweep can't kill the loop). Runs until `stop_event` is set.
    """
    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as db:
                await fail_stale_running_runs(db)
        except Exception:
            logger.exception("stale_run_sweep_failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval.total_seconds())


def cancel_run(run_id: uuid.UUID) -> bool:
    """Request cancellation of a run's in-flight background task.

    Best-effort: asyncio.Task.cancel() raises CancelledError at the task's
    next await point, it doesn't stop it instantly. Returns True if a task
    was found and cancellation was requested, False if the run has no
    tracked in-flight task (already finished, never backgrounded, or
    still queued and not yet claimed by a worker — a queued-but-unclaimed
    job has no task to cancel; deleting/marking that job cancelled before
    a worker claims it is a different operation, not yet implemented).
    """
    task = _tasks_by_run_id.get(run_id)
    if task is None:
        return False
    task.cancel()
    return True


# ---------------------------------------------------------------------------
# Title generation — same fire-and-forget shape run/resume execution used to
# have, for a much smaller job: one LLM call that only ever patches one
# column. Deliberately NOT migrated onto the durable queue: losing a
# not-yet-finished title generation to a crash leaves the row at its
# already-committed deterministic fallback title (see
# app.agents.title_generation.generate_title) — never worse than today's
# own failure path, and never a customer-visible loss of real work the way
# a lost agent run or indexing job would be. KAN-18's acceptance criteria
# name "agent runs and indexing jobs" specifically; this is neither.
# ---------------------------------------------------------------------------

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

    task = asyncio.create_task(
        _generate_title_task(model_cls or Workflow, row_id, objective, model)
    )
    _title_tasks.add(task)
    task.add_done_callback(_title_tasks.discard)
    return task
