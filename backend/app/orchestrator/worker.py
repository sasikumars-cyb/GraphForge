"""`Worker` — claims jobs from the durable queue and dispatches them to a
registered handler.

Deliberately does not itself execute a job's actual work inline inside the
claim transaction: `claim_next` commits and returns a plain `BackgroundJob`
row, and the handler runs afterward, in its own `asyncio.create_task` (see
`run_once`) — the same "own its own session, survive the caller's session
closing" shape `_execute_run_task`/`_resume_step_task` already had before
this ticket, preserved rather than collapsed into the claim transaction so a
slow handler never holds the row's lock and blocks every other worker's
`claim_next` behind it.

No heartbeat mechanism exists yet: a handler that runs longer than
`DEFAULT_LEASE_SECONDS` risks a second worker reclaiming and re-running its
job concurrently. Every registered handler in this codebase today
(`_execute_run_task`, `_resume_step_task`, indexing) is written to be safe
to re-run from scratch on the same row (re-fetches by id, checks status,
never assumes it is the only writer) — so a duplicate run produces wasted
work, not corrupted data, which is why this is an accepted v1 gap rather
than a blocker. A future heartbeat would let the lease be much shorter than
1800s without that risk; tracked as a known follow-up, not silently
forgotten.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable

from app.database.session import AsyncSessionLocal
from app.orchestrator.job_queue import JobQueue

logger = logging.getLogger(__name__)

JobHandler = Callable[[dict[str, object]], Awaitable[None]]

# Populated by each handler module at import time (see
# `app.orchestrator.background_execution`'s and
# `app.indexer.workers.index_worker`'s own bottom-of-module
# `register_handler(...)` calls) — the same declarative-registry shape
# `app.tools.registry`/`app.orchestrator.registry` already use elsewhere in
# this codebase, not a new pattern invented for this ticket.
_HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str, handler: JobHandler) -> None:
    if job_type in _HANDLERS and _HANDLERS[job_type] is not handler:
        raise ValueError(f"A handler is already registered for job_type={job_type!r}")
    _HANDLERS[job_type] = handler


def registered_job_types() -> frozenset[str]:
    return frozenset(_HANDLERS)


def _default_worker_id() -> str:
    import os

    return f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    """One poll/claim/dispatch loop. `worker_id` defaults to
    `hostname:pid` — real enough to tell two workers apart in
    `leased_by` when debugging an abandoned lease, without needing any
    configuration for the common single-worker-per-process case."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        job_types: set[str] | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._worker_id = worker_id or _default_worker_id()
        self._job_types = job_types
        self._poll_interval_seconds = poll_interval_seconds

    async def run_once(self) -> bool:
        """Claim and execute exactly one job, if one is available.
        Returns whether a job was found — the primitive both
        `run_forever`'s loop and tests build on, since a test can call this
        directly without a sleep loop or a stop-condition to orchestrate.
        """
        async with AsyncSessionLocal() as db:
            job = await JobQueue(db).claim_next(self._worker_id, job_types=self._job_types)

        if job is None:
            return False

        await self._execute_claimed_job(job.id, job.job_type, dict(job.payload))
        return True

    async def _execute_claimed_job(
        self, job_id: uuid.UUID, job_type: str, payload: dict[str, object]
    ) -> None:
        handler = _HANDLERS.get(job_type)
        if handler is None:
            # A job_type nothing in this process knows how to run — e.g. a
            # deploy where an older job was enqueued by newer code, or a
            # genuine bug. Failing it (with retry/dead-letter exactly as
            # any other failure) rather than dropping it silently keeps it
            # visible and, if a compatible worker starts up before
            # max_attempts is exhausted, still recoverable.
            logger.error("background_job_no_handler job_id=%s job_type=%s", job_id, job_type)
            async with AsyncSessionLocal() as db:
                await JobQueue(db).fail(job_id, f"No handler registered for job_type={job_type!r}")
            return

        try:
            await handler(payload)
        except Exception as exc:  # noqa: BLE001 — every failure must reach JobQueue.fail
            logger.exception("background_job_failed job_id=%s job_type=%s", job_id, job_type)
            async with AsyncSessionLocal() as db:
                await JobQueue(db).fail(job_id, str(exc)[:4000])
            return

        async with AsyncSessionLocal() as db:
            await JobQueue(db).complete(job_id)

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        """Poll indefinitely until `stop_event` is set (or forever, if
        none is given — the real deployment shape, where the process
        itself is what stops the loop). Each claimed job runs as its own
        `asyncio.create_task` so a slow job never delays the next poll —
        this loop's only job is claiming and dispatching, never awaiting a
        handler to completion before claiming again.

        A failure to even *claim* (e.g. Postgres is momentarily
        unreachable) is caught and logged, not left to end the loop — a
        worker that stops polling forever after one transient DB blip
        would be a worse durability story than the one this ticket exists
        to fix. Backs off for one poll interval before retrying, the same
        as finding no job.
        """
        stop_event = stop_event or asyncio.Event()
        in_flight: set[asyncio.Task[None]] = set()

        while not stop_event.is_set():
            try:
                async with AsyncSessionLocal() as db:
                    job = await JobQueue(db).claim_next(self._worker_id, job_types=self._job_types)
            except Exception:
                logger.exception("background_job_claim_failed worker_id=%s", self._worker_id)
                job = None

            if job is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval_seconds)
                continue

            task = asyncio.create_task(
                self._execute_claimed_job(job.id, job.job_type, dict(job.payload))
            )
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)

        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)


async def reclaim_expired_leases_once() -> int:
    """Run `JobQueue.reclaim_expired_leases` once — the queue-side half of
    startup recovery, meant to be called alongside
    `background_execution.recover_orphaned_runs` from `app.main`'s
    lifespan (see that function's own docstring for why a startup sweep,
    not only lazy reclaim-on-claim, matters for observability)."""
    async with AsyncSessionLocal() as db:
        return await JobQueue(db).reclaim_expired_leases()
