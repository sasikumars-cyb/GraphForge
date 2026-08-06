"""`JobQueue` — the durable-queue half of KAN-18.

Everything a caller needs to enqueue durable work and everything a `Worker`
(worker.py) needs to claim, complete, fail, and reclaim it. No polling loop
lives here — this module is pure data-layer operations against
`background_jobs`, kept separate from the poll/dispatch loop in `worker.py`
the same way `app.knowledge_engine.memory_service` stays separate from
anything that decides *when* to call it.

The claim algorithm (`claim_next`) is the standard durable-queue pattern:
`SELECT ... FOR UPDATE SKIP LOCKED` so concurrent workers never claim the
same row (no distributed lock service needed — Postgres's own row locking
is enough at this scale), immediately followed by marking the row `leased`
with an expiring lease in the same transaction. A worker that crashes after
claiming but before completing leaves the row `leased` with a
`lease_expires_at` in the past once that time passes; `reclaim_expired_leases`
is what makes such a row claimable again — this, not anything worker-side,
is what "a worker crash/restart resumes or retries in-flight jobs without
data loss" actually means at the data layer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.background_job import BackgroundJob

# Generous by design rather than tuned: v1 has no heartbeat mechanism (a
# long-running job cannot yet extend its own lease mid-execution — see
# worker.py's module docstring), so the lease must outlast the slowest
# realistic single job or a still-healthy worker's job would be wrongly
# reclaimed and double-executed. Revisit downward only once a heartbeat
# exists to make a shorter lease safe.
DEFAULT_LEASE_SECONDS = 1800


class JobQueue:
    """Thin wrapper around one `AsyncSession` — callers own the session's
    lifecycle and commit/rollback exactly as they do for every other
    repository-shaped class in this codebase (see e.g.
    `EngineeringMemoryService`)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, object],
        *,
        max_attempts: int = 3,
        correlation_id: str | None = None,
    ) -> BackgroundJob:
        """Insert a new job, `status="queued"`.

        `payload` is passed through `fastapi.encoders.jsonable_encoder` —
        the same tool FastAPI itself uses to turn Pydantic models,
        dataclasses, UUIDs, and datetimes into JSON-safe primitives for a
        response body — before being written to the `JSON` column, so a
        caller may hand this a `Subject` (a Pydantic model) or a raw dict
        containing UUIDs without doing that conversion itself. This is
        enforced here, at the one place every job's payload is written,
        rather than trusted of each of the several call sites that enqueue
        one.

        Commits immediately, deliberately — every current call site already
        commits its own Run-row mutations (e.g. `run.status`) before
        scheduling execution, a structure this module keeps rather than
        reorders across every caller. That leaves a small, non-zero window
        between "the Run is committed" and "the job to execute it is
        committed" — real, but a strict improvement over the window this
        replaces (an in-memory `asyncio.create_task()` call, durable for
        exactly as long as the process stays up). A caller that genuinely
        needs the two to land in one transaction may still call this before
        its own commit and rely on the flush this performs internally —
        nothing here prevents that, this default just doesn't require it.
        """
        job = BackgroundJob(
            job_type=job_type,
            payload=jsonable_encoder(payload),
            status="queued",
            attempts=0,
            max_attempts=max_attempts,
            correlation_id=correlation_id,
        )
        self._db.add(job)
        await self._db.commit()
        await self._db.refresh(job)
        return job

    async def claim_next(
        self,
        worker_id: str,
        *,
        job_types: set[str] | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> BackgroundJob | None:
        """Atomically claim and return the oldest eligible job, or `None`.

        Eligible means `status="queued"`, or `status="leased"` with an
        expired lease (a prior worker that never came back) — both are
        claimable by design; `reclaim_expired_leases` exists as a separate,
        explicit operation only so a caller can run it as a standalone
        sweep (e.g. from a periodic task) without also claiming what it
        finds, not because claiming itself needs it as a precondition.

        Commits before returning: the lease must be visible to every other
        worker's own `claim_next` immediately, not only once the caller
        gets around to committing its own unrelated work.
        """
        now = datetime.now(UTC)
        result = await self._db.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.status == "queued"
                if job_types is None
                else (BackgroundJob.status == "queued") & (BackgroundJob.job_type.in_(job_types))
            )
            .order_by(BackgroundJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = result.scalar_one_or_none()

        if job is None:
            # No fresh job — fall back to one whose lease already expired.
            # Split into two queries rather than one OR'd predicate: the
            # common case (a fresh job exists) never pays for evaluating
            # the lease-expiry branch at all.
            expired_query = (
                select(BackgroundJob)
                .where(BackgroundJob.status == "leased", BackgroundJob.lease_expires_at < now)
                .order_by(BackgroundJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job_types is not None:
                expired_query = expired_query.where(BackgroundJob.job_type.in_(job_types))
            job = (await self._db.execute(expired_query)).scalar_one_or_none()

        if job is None:
            return None

        job.status = "leased"
        job.attempts += 1
        job.leased_by = worker_id
        job.leased_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        await self._db.commit()
        await self._db.refresh(job)
        return job

    async def complete(self, job_id: uuid.UUID) -> None:
        """Mark a job done. Commits — a completed job must never be
        re-claimed by a race with `reclaim_expired_leases` after this
        returns."""
        await self._db.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id)
            .values(status="completed", completed_at=datetime.now(UTC))
        )
        await self._db.commit()

    async def fail(self, job_id: uuid.UUID, error: str) -> None:
        """Record a failure. Requeues (`status="queued"`) if `attempts`
        is still under `max_attempts`, otherwise dead-letters — this is
        the one place retry-vs-give-up is decided, so every failure path
        (an exception in the handler, an explicit reclaim-then-fail) goes
        through it rather than each caller re-deriving the threshold
        check."""
        job = await self._db.get(BackgroundJob, job_id)
        if job is None:
            return
        job.last_error = error[:4000]
        job.status = "queued" if job.attempts < job.max_attempts else "dead_letter"
        job.leased_by = None
        job.leased_at = None
        job.lease_expires_at = None
        await self._db.commit()

    async def reclaim_expired_leases(self) -> int:
        """Requeue every `leased` job whose lease has expired, without
        claiming any of them — the sweep half of crash recovery, meant to
        run once at process startup (mirroring
        `background_execution.recover_orphaned_runs`'s own startup-sweep
        role) and optionally on a timer thereafter. Returns the count
        reclaimed, for logging.

        Distinct from letting `claim_next` discover expired leases lazily:
        a startup sweep surfaces "N jobs were abandoned by a dead worker"
        as a single observable event, rather than that fact only becoming
        visible one `claim_next` call at a time.
        """
        now = datetime.now(UTC)
        result = await self._db.execute(
            select(BackgroundJob).where(
                BackgroundJob.status == "leased", BackgroundJob.lease_expires_at < now
            )
        )
        expired = list(result.scalars().all())
        for job in expired:
            job.status = "queued"
            job.leased_by = None
            job.leased_at = None
            job.lease_expires_at = None
        if expired:
            await self._db.commit()
        return len(expired)
