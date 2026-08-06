"""Integration tests for app.orchestrator.job_queue.JobQueue — real Postgres.

Covers the durable-queue primitives KAN-18 exists to add: enqueue produces
a JSON-safe, committed row; claim_next is exclusive under concurrency
(SELECT ... FOR UPDATE SKIP LOCKED); complete/fail transition status
correctly, including the retry-vs-dead-letter threshold; and
reclaim_expired_leases requeues what a crashed worker abandoned — the
data-layer half of "a worker crash/restart resumes or retries in-flight
jobs without data loss."
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.background_job import BackgroundJob
from app.orchestrator.job_queue import JobQueue

pytestmark = pytest.mark.asyncio


class TestEnqueue:
    async def test_enqueue_persists_a_queued_job(self, db_session: AsyncSession) -> None:
        job = await JobQueue(db_session).enqueue("run_execution", {"run_id": "abc"})

        assert job.status == "queued"
        assert job.attempts == 0
        assert job.max_attempts == 3
        fetched = await db_session.get(BackgroundJob, job.id)
        assert fetched is not None
        assert fetched.payload == {"run_id": "abc"}

    async def test_enqueue_json_safes_uuids_via_jsonable_encoder(
        self, db_session: AsyncSession
    ) -> None:
        """Real reason this matters: `subject`/`extras` carry UUIDs and
        Pydantic models at every real call site — a payload that can't
        round-trip through JSON would only be discovered the first time a
        worker tried to claim it, not at enqueue time."""
        some_id = uuid.uuid4()
        job = await JobQueue(db_session).enqueue("run_execution", {"run_id": some_id})

        assert job.payload == {"run_id": str(some_id)}

    async def test_enqueue_records_correlation_id(self, db_session: AsyncSession) -> None:
        job = await JobQueue(db_session).enqueue(
            "run_execution", {"run_id": "abc"}, correlation_id="run-42"
        )
        assert job.correlation_id == "run-42"

    async def test_enqueue_respects_custom_max_attempts(self, db_session: AsyncSession) -> None:
        job = await JobQueue(db_session).enqueue("index_repository", {}, max_attempts=5)
        assert job.max_attempts == 5


class TestClaimNext:
    async def test_claim_next_returns_none_when_empty(self, db_session: AsyncSession) -> None:
        assert await JobQueue(db_session).claim_next("worker-1") is None

    async def test_claim_next_marks_the_job_leased(self, db_session: AsyncSession) -> None:
        queue = JobQueue(db_session)
        enqueued = await queue.enqueue("run_execution", {"run_id": "abc"})

        claimed = await queue.claim_next("worker-1")

        assert claimed is not None
        assert claimed.id == enqueued.id
        assert claimed.status == "leased"
        assert claimed.attempts == 1
        assert claimed.leased_by == "worker-1"
        assert claimed.lease_expires_at is not None
        assert claimed.lease_expires_at > datetime.now(UTC)

    async def test_claim_next_is_fifo(self, db_session: AsyncSession) -> None:
        queue = JobQueue(db_session)
        first = await queue.enqueue("run_execution", {"n": 1})
        await asyncio.sleep(0.01)
        second = await queue.enqueue("run_execution", {"n": 2})

        claimed_first = await queue.claim_next("worker-1")
        claimed_second = await queue.claim_next("worker-1")

        assert claimed_first is not None
        assert claimed_first.id == first.id
        assert claimed_second is not None
        assert claimed_second.id == second.id

    async def test_claim_next_respects_job_types_filter(self, db_session: AsyncSession) -> None:
        queue = JobQueue(db_session)
        await queue.enqueue("index_repository", {})
        run_job = await queue.enqueue("run_execution", {"run_id": "abc"})

        claimed = await queue.claim_next("worker-1", job_types={"run_execution"})

        assert claimed is not None
        assert claimed.id == run_job.id

    async def test_claim_next_does_not_reclaim_a_fresh_lease(
        self, db_session: AsyncSession
    ) -> None:
        """A job leased moments ago, well within its lease window, must not
        be handed to a second worker — that would be double-execution, not
        recovery."""
        queue = JobQueue(db_session)
        await queue.enqueue("run_execution", {"run_id": "abc"})
        await queue.claim_next("worker-1")

        second_claim = await queue.claim_next("worker-2")

        assert second_claim is None

    async def test_claim_next_reclaims_an_expired_lease(self, db_session: AsyncSession) -> None:
        queue = JobQueue(db_session)
        job = await queue.enqueue("run_execution", {"run_id": "abc"})
        await queue.claim_next("worker-1", lease_seconds=0)
        # lease_seconds=0 means lease_expires_at == "now" at claim time;
        # by the time this next call runs, it is already in the past.

        reclaimed = await queue.claim_next("worker-2")

        assert reclaimed is not None
        assert reclaimed.id == job.id
        assert reclaimed.leased_by == "worker-2"
        assert reclaimed.attempts == 2  # incremented on every claim, including a reclaim


class TestCompleteAndFail:
    async def test_complete_marks_the_job_done(self, db_session: AsyncSession) -> None:
        queue = JobQueue(db_session)
        job = await queue.enqueue("run_execution", {"run_id": "abc"})
        await queue.claim_next("worker-1")

        await queue.complete(job.id)

        completed = await db_session.get(BackgroundJob, job.id)
        assert completed is not None
        assert completed.status == "completed"
        assert completed.completed_at is not None

    async def test_fail_requeues_when_attempts_remain(self, db_session: AsyncSession) -> None:
        queue = JobQueue(db_session)
        job = await queue.enqueue("run_execution", {"run_id": "abc"}, max_attempts=3)
        await queue.claim_next("worker-1")

        await queue.fail(job.id, "boom")

        failed = await db_session.get(BackgroundJob, job.id)
        assert failed is not None
        assert failed.status == "queued"
        assert failed.last_error == "boom"
        assert failed.leased_by is None

    async def test_fail_dead_letters_once_attempts_are_exhausted(
        self, db_session: AsyncSession
    ) -> None:
        queue = JobQueue(db_session)
        job = await queue.enqueue("run_execution", {"run_id": "abc"}, max_attempts=1)
        await queue.claim_next("worker-1")

        await queue.fail(job.id, "boom")

        failed = await db_session.get(BackgroundJob, job.id)
        assert failed is not None
        assert failed.status == "dead_letter"

    async def test_fail_truncates_a_very_long_error(self, db_session: AsyncSession) -> None:
        queue = JobQueue(db_session)
        job = await queue.enqueue("run_execution", {"run_id": "abc"})
        await queue.claim_next("worker-1")

        await queue.fail(job.id, "x" * 10_000)

        failed = await db_session.get(BackgroundJob, job.id)
        assert failed is not None
        assert failed.last_error is not None
        assert len(failed.last_error) == 4000


class TestReclaimExpiredLeases:
    async def test_reclaim_requeues_without_claiming(self, db_session: AsyncSession) -> None:
        queue = JobQueue(db_session)
        job = await queue.enqueue("run_execution", {"run_id": "abc"})
        await queue.claim_next("worker-1", lease_seconds=0)

        count = await queue.reclaim_expired_leases()

        assert count == 1
        row = await db_session.get(BackgroundJob, job.id)
        assert row is not None
        assert row.status == "queued"
        assert row.leased_by is None
        assert row.lease_expires_at is None

    async def test_reclaim_ignores_jobs_with_a_fresh_lease(self, db_session: AsyncSession) -> None:
        queue = JobQueue(db_session)
        await queue.enqueue("run_execution", {"run_id": "abc"})
        await queue.claim_next("worker-1")

        assert await queue.reclaim_expired_leases() == 0

    async def test_reclaim_ignores_already_terminal_jobs(self, db_session: AsyncSession) -> None:
        queue = JobQueue(db_session)
        job = await queue.enqueue("run_execution", {"run_id": "abc"})
        await queue.claim_next("worker-1")
        await queue.complete(job.id)

        assert await queue.reclaim_expired_leases() == 0

    async def test_a_reclaimed_job_can_be_claimed_again(self, db_session: AsyncSession) -> None:
        """The actual crash-recovery path end to end: claim, abandon
        (never complete/fail — simulating a killed worker), reclaim,
        re-claim by a different worker."""
        queue = JobQueue(db_session)
        job = await queue.enqueue("run_execution", {"run_id": "abc"})
        await queue.claim_next("worker-1", lease_seconds=0)

        reclaimed_count = await queue.reclaim_expired_leases()
        assert reclaimed_count == 1

        second_claim = await queue.claim_next("worker-2")
        assert second_claim is not None
        assert second_claim.id == job.id
        assert second_claim.leased_by == "worker-2"


class TestConcurrentClaims:
    async def test_two_workers_never_claim_the_same_job(self) -> None:
        """The actual guarantee `FOR UPDATE SKIP LOCKED` provides — two
        concurrent claims against one queued job must not both succeed.

        Deliberately does not use the `db_session` fixture: its rollback
        wrapper keeps every write inside one uncommitted, savepoint-nested
        transaction on a single connection, which a second, genuinely
        independent connection (what two real worker processes each hold)
        would never see — the exact cross-connection-visibility problem
        `test_agent_orchestrator_api.py`'s own module docstring documents
        for the same reason. Uses `app.database.session.AsyncSessionLocal`
        directly, on real, separate connections, with explicit cleanup
        since nothing here rolls back automatically.
        """
        from app.database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as setup_session:
            job = await JobQueue(setup_session).enqueue("run_execution", {"run_id": "abc"})
            job_id = job.id

        try:

            async def _claim(worker_id: str) -> BackgroundJob | None:
                async with AsyncSessionLocal() as session:
                    return await JobQueue(session).claim_next(worker_id)

            results = await asyncio.gather(_claim("worker-a"), _claim("worker-b"))
            claimed = [r for r in results if r is not None]
            assert len(claimed) == 1
            assert claimed[0].id == job_id
        finally:
            async with AsyncSessionLocal() as cleanup_session:
                row = await cleanup_session.get(BackgroundJob, job_id)
                if row is not None:
                    await cleanup_session.delete(row)
                    await cleanup_session.commit()
