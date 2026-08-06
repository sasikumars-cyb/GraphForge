"""Integration tests for app.orchestrator.worker.Worker — real Postgres.

`TestKillWorkerMidRun` is the load/chaos scenario KAN-18's acceptance
criteria name explicitly: "kill the worker mid-run, verify recovery." It's
simulated rather than a literal subprocess kill (killing a real OS process
mid-test is its own kind of flaky) — the process-crash's actual, durable
consequence is a `BackgroundJob` left `leased` with an expired lease and no
one left to finish it, which is exactly the state this test constructs
directly: claim a job, never call complete/fail on it (the crash), let its
lease expire, then prove a second worker can pick up the exact same job and
finish it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import AsyncSessionLocal
from app.models.background_job import BackgroundJob
from app.orchestrator.job_queue import JobQueue
from app.orchestrator.worker import Worker, register_handler, registered_job_types

pytestmark = pytest.mark.asyncio

_TEST_JOB_TYPE = "test_worker_job"


@pytest.fixture(autouse=True)
def _register_test_handler(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`register_handler` refuses to silently overwrite a handler for the
    same job_type (see its own docstring) — patch the module-level
    registry dict directly per test instead of calling it twice across
    the file, so each test can install exactly the fake handler it needs."""
    import app.orchestrator.worker as worker_module

    monkeypatch.setattr(worker_module, "_HANDLERS", dict(worker_module._HANDLERS))
    yield


@pytest.fixture(autouse=True)
async def _clean_background_jobs() -> AsyncIterator[None]:
    """These tests deliberately bypass the rollback-based `db_session`
    fixture (see TestKillWorkerMidRun's module-level rationale) to get
    real cross-connection visibility — which means nothing rolls their
    writes back automatically. A `background_jobs` row left `queued` by
    one test (e.g. a fail-and-retry test that never lets its job reach a
    terminal state) is exactly the kind of row `claim_next`'s FIFO
    ordering would hand to the *next* test instead of the job it just
    enqueued. Delete every row this file's own job types could have
    touched, both before and after each test.
    """
    from sqlalchemy import delete

    async def _clean() -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.job_type.in_((_TEST_JOB_TYPE, "nonexistent_job_type_xyz"))
                )
            )
            await db.commit()

    await _clean()
    yield
    await _clean()


class TestRunOnce:
    async def test_run_once_returns_false_when_queue_is_empty(
        self, db_session: AsyncSession
    ) -> None:
        assert await Worker(job_types={_TEST_JOB_TYPE}).run_once() is False

    async def test_run_once_claims_and_completes_a_job(self) -> None:
        calls: list[dict[str, object]] = []

        async def handler(payload: dict[str, object]) -> None:
            calls.append(payload)

        register_handler(_TEST_JOB_TYPE, handler)

        async with AsyncSessionLocal() as db:
            job = await JobQueue(db).enqueue(_TEST_JOB_TYPE, {"n": 1})

        found = await Worker(job_types={_TEST_JOB_TYPE}).run_once()

        assert found is True
        assert calls == [{"n": 1}]
        async with AsyncSessionLocal() as db:
            row = await db.get(BackgroundJob, job.id)
            assert row is not None
            assert row.status == "completed"

    async def test_run_once_fails_and_retries_on_handler_exception(self) -> None:
        attempt_count = 0

        async def handler(payload: dict[str, object]) -> None:
            nonlocal attempt_count
            attempt_count += 1
            raise RuntimeError("handler bug")

        register_handler(_TEST_JOB_TYPE, handler)

        async with AsyncSessionLocal() as db:
            job = await JobQueue(db).enqueue(_TEST_JOB_TYPE, {}, max_attempts=3)

        await Worker(job_types={_TEST_JOB_TYPE}).run_once()

        async with AsyncSessionLocal() as db:
            row = await db.get(BackgroundJob, job.id)
            assert row is not None
            assert row.status == "queued"  # requeued, one attempt remains
            assert row.last_error is not None
            assert "handler bug" in row.last_error
        assert attempt_count == 1

    async def test_run_once_dead_letters_after_exhausting_attempts(self) -> None:
        async def handler(payload: dict[str, object]) -> None:
            raise RuntimeError("always fails")

        register_handler(_TEST_JOB_TYPE, handler)

        async with AsyncSessionLocal() as db:
            job = await JobQueue(db).enqueue(_TEST_JOB_TYPE, {}, max_attempts=1)

        await Worker(job_types={_TEST_JOB_TYPE}).run_once()

        async with AsyncSessionLocal() as db:
            row = await db.get(BackgroundJob, job.id)
            assert row is not None
            assert row.status == "dead_letter"

    async def test_run_once_fails_a_job_with_no_registered_handler(self) -> None:
        """A job_type nothing in this process can run — surfaced as a
        failure (retryable/dead-letterable like any other), not dropped
        silently, so a compatible worker starting later can still recover
        it."""
        async with AsyncSessionLocal() as db:
            job = await JobQueue(db).enqueue("nonexistent_job_type_xyz", {}, max_attempts=1)

        await Worker(job_types={"nonexistent_job_type_xyz"}).run_once()

        async with AsyncSessionLocal() as db:
            row = await db.get(BackgroundJob, job.id)
            assert row is not None
            assert row.status == "dead_letter"
            assert row.last_error is not None
            assert "No handler registered" in row.last_error


class TestKillWorkerMidRun:
    """KAN-18 acceptance criterion, verbatim: "Load/chaos test: kill the
    worker mid-run, verify recovery." """

    async def test_a_job_abandoned_by_a_crashed_worker_is_recovered_by_another(self) -> None:
        completed_by: list[str] = []

        async def handler(payload: dict[str, object]) -> None:
            completed_by.append(str(payload["marker"]))

        register_handler(_TEST_JOB_TYPE, handler)

        async with AsyncSessionLocal() as db:
            job = await JobQueue(db).enqueue(_TEST_JOB_TYPE, {"marker": "job-1"})

        # Simulate the crash: claim the job (a worker has started it) and
        # then do nothing further — no complete(), no fail(), exactly what
        # a killed process leaves behind. lease_seconds=0 stands in for
        # "time has passed since the crash," not an instant reclaim.
        async with AsyncSessionLocal() as db:
            crashed_claim = await JobQueue(db).claim_next("crashed-worker", lease_seconds=0)
        assert crashed_claim is not None
        assert crashed_claim.id == job.id

        # Startup recovery, as app.main's lifespan runs it.
        from app.orchestrator.worker import reclaim_expired_leases_once

        reclaimed = await reclaim_expired_leases_once()
        assert reclaimed >= 1

        # A fresh worker — the restarted process — now finishes the job
        # the crashed one abandoned.
        recovered = await Worker(
            worker_id="recovered-worker", job_types={_TEST_JOB_TYPE}
        ).run_once()

        assert recovered is True
        assert completed_by == ["job-1"]
        async with AsyncSessionLocal() as db:
            row = await db.get(BackgroundJob, job.id)
            assert row is not None
            assert row.status == "completed"
            # attempts: 1 for the crashed claim, 1 for the recovering claim.
            assert row.attempts == 2

    async def test_run_forever_recovers_an_expired_lease_without_an_explicit_reclaim_call(
        self,
    ) -> None:
        """`claim_next` itself treats an expired lease as claimable (see
        its own docstring) — a running worker's normal poll loop recovers
        an abandoned job even before any startup-sweep runs, not only
        after one. Exercises `run_forever` end to end rather than
        `run_once`, since this is the path a real deployment relies on."""
        completed = asyncio.Event()

        async def handler(payload: dict[str, object]) -> None:
            completed.set()

        register_handler(_TEST_JOB_TYPE, handler)

        async with AsyncSessionLocal() as db:
            job = await JobQueue(db).enqueue(_TEST_JOB_TYPE, {})
            # Directly construct an already-expired lease, rather than
            # claiming with lease_seconds=0 then waiting — deterministic,
            # no timing dependency.
            row = await db.get(BackgroundJob, job.id)
            assert row is not None
            row.status = "leased"
            row.leased_by = "crashed-worker"
            row.leased_at = datetime.now(UTC) - timedelta(hours=1)
            row.lease_expires_at = datetime.now(UTC) - timedelta(minutes=30)
            await db.commit()

        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(
            Worker(job_types={_TEST_JOB_TYPE}, poll_interval_seconds=0.02).run_forever(stop_event)
        )
        try:
            await asyncio.wait_for(completed.wait(), timeout=2)
        finally:
            stop_event.set()
            await worker_task

        async with AsyncSessionLocal() as db:
            final = await db.get(BackgroundJob, job.id)
            assert final is not None
            assert final.status == "completed"


class TestRegisterHandler:
    async def test_register_handler_rejects_a_conflicting_second_registration(self) -> None:
        async def handler_one(payload: dict[str, object]) -> None:
            pass

        async def handler_two(payload: dict[str, object]) -> None:
            pass

        register_handler(_TEST_JOB_TYPE, handler_one)
        with pytest.raises(ValueError, match="already registered"):
            register_handler(_TEST_JOB_TYPE, handler_two)

    async def test_register_handler_is_idempotent_for_the_same_handler(self) -> None:
        async def handler(payload: dict[str, object]) -> None:
            pass

        register_handler(_TEST_JOB_TYPE, handler)
        register_handler(_TEST_JOB_TYPE, handler)  # must not raise

    async def test_registered_job_types_reflects_real_startup_registrations(self) -> None:
        """`registered_job_types()` must reflect a registration made just
        now, in this test — proving it reads the live registry rather than
        some cached snapshot. Whether the real production handler modules
        (`run_execution`/`resume_execution`/`index_repository`) are
        registered by the time a worker actually starts is asserted
        separately in tests/integration/test_background_execution_scheduling.py,
        where those modules are guaranteed imported."""

        async def handler(payload: dict[str, object]) -> None:
            pass

        register_handler(_TEST_JOB_TYPE, handler)
        assert _TEST_JOB_TYPE in registered_job_types()
