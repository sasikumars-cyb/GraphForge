"""Shared pytest fixtures."""

import asyncio
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import rate_limit
from app.database.base import Base
from app.database.session import AsyncSessionLocal, engine, get_db_session
from app.main import create_app
from app.models.background_job import BackgroundJob
from app.orchestrator.worker import Worker


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> None:
    """`app.core.rate_limit._hits` is a module-level, process-global dict —
    unlike the DB (rolled back per test via `db_session`), it is NOT reset
    between tests. Several test files reuse the same fixed email (e.g.
    "ada@example.com" across test_auth.py/test_repositories.py/
    test_webhooks.py/test_github_oauth.py), each logging in at least once;
    with login now rate-limited (see auth.py), enough of those accumulating
    within the suite's own runtime (well under the 5-minute window) trips
    the limit and fails unrelated, later tests with a 429 instead of their
    expected response. Every test gets a clean rate-limit table regardless.
    """
    rate_limit._hits.clear()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """A plain client against a fresh app instance.

    Historically documented as "no database involved" / "independent of
    Postgres being reachable at all," for tests that only exercise routing
    or error handling (health, 404s). That is no longer quite true as of
    KAN-18: `create_app()` alone no longer makes scheduled agent runs/
    indexing jobs execute — `schedule_run_execution`/`schedule_indexing_job`
    now only enqueue a durable `BackgroundJob` row (see
    `app.orchestrator.background_execution`'s module docstring); a `Worker`
    has to actually claim it. `httpx.ASGITransport` never runs the app's own
    lifespan (confirmed: `recover_orphaned_runs` was always tested by calling
    it directly, never by relying on lifespan firing under this fixture), so
    an embedded worker is started here explicitly for the fixture's
    lifetime — the same role `app.main`'s lifespan-started worker plays in a
    real deployment, just scoped to one test — and that worker, plus the
    leftover-row cleanup below, both need Postgres reachable to set up. A
    genuinely DB-free variant of this fixture could still be split out if a
    real need for one shows up; nothing currently in the suite asks for it.
    A short poll interval keeps job pickup within the wall-clock budget
    every existing polling helper (`_poll_run_until_terminal` et al., 0.05s
    cadence) already tolerates.
    """
    # `background_jobs` is real, committed, and shared across every test
    # using this fixture in the same run (unlike `db_session`'s per-test
    # rollback) — a row left `queued`/`leased` by an earlier test (one that
    # intentionally never drove a job to a terminal state, or that failed
    # mid-flow) is otherwise exactly the row this test's own worker would
    # claim *first*, via `claim_next`'s FIFO ordering, ahead of whatever
    # this test itself enqueues. Cleared before, not only after, so a test
    # run that stops mid-suite (Ctrl-C, a debugger) still leaves the next
    # run starting clean. `create_all` first (idempotent, same as
    # `db_session`'s own) since a no-DB test using this fixture (health,
    # error handling) may be the very first test to touch Postgres at all.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(BackgroundJob).where(BackgroundJob.status.in_(("queued", "leased")))
        )
        await db.commit()

    app = create_app()
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(Worker(poll_interval_seconds=0.02).run_forever(stop_event))
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        # Drain before stopping, not just stop-then-await: a job this test
        # enqueued but that finished its body without ever polling for (a
        # test that only cares about the 202, or a fixture that enqueues
        # incidentally as setup) can still be sitting `queued` right now.
        # Stopping the worker immediately would leave that row for
        # whichever *later* test's own fresh worker happens to claim it
        # first — against rows this test's own teardown may have already
        # deleted. Real failure mode, not theoretical: surfaced as a
        # ForeignKeyViolationError in an unrelated test file the first time
        # this fixture shipped without a drain step.
        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            async with AsyncSessionLocal() as db:
                pending = (
                    await db.execute(
                        select(BackgroundJob.id).where(
                            BackgroundJob.status.in_(("queued", "leased"))
                        )
                    )
                ).first()
            if pending is None:
                break
            await asyncio.sleep(0.02)
        stop_event.set()
        await worker_task


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A DB session scoped to one test, wrapped in a transaction that's
    always rolled back at teardown — so DB-touching tests never leave data
    behind, even when run against a shared dev database. `create_all` is
    idempotent, so this doesn't depend on `alembic upgrade head` having run.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with engine.connect() as conn:
        await conn.begin()
        session_factory = async_sessionmaker(
            bind=conn,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        async with session_factory() as session:
            yield session
        await conn.rollback()


@pytest.fixture
async def db_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Like `client`, but `get_db_session` is overridden to the
    transactional `db_session` above. Use this for anything that touches
    the database (currently: auth).
    """

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
