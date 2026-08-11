"""Shared pytest fixtures."""

import asyncio
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from alembic.config import Config as AlembicConfig

from alembic import command as alembic_command
from app.core.config import Settings


# --- Route every test run at its own database, never the dev one --------
#
# `test_agent_orchestrator_api.py`'s docstring explains why the `client`
# fixture below uses real, committed writes rather than the transactional
# `db_session`: background execution opens its own independent
# AsyncSessionLocal(), and a rolled-back transaction is invisible to it.
# That's a genuine constraint, not an oversight — but "real, committed
# writes" against whatever DATABASE_URL happens to resolve to means every
# local `pytest` run against the docker-compose dev stack was leaving its
# fixture rows (e.g. "Add exponential backoff to the retry handler",
# "Implement JWT auth") permanently in the same Postgres database the dev
# UI reads from. Confirmed: thousands of leftover workflow rows in the dev
# DB traced back to exactly these fixture titles.
#
# Fix: derive a same-server-different-database URL (`<name>_test`, or
# `TEST_DATABASE_URL` if explicitly set) *before* any `app.*` module runs
# `get_settings()` — which is `@lru_cache`d, so whichever `DATABASE_URL`
# is in `os.environ` the first time it's called wins for the rest of the
# process. Creating the database on demand (rather than requiring a
# one-time manual step) means this self-heals identically in CI's
# ephemeral Postgres service container and in a developer's persistent
# docker-compose one.
def _test_database_url() -> str:
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        return override
    # `Settings()` (uncached, unlike `get_settings()`) reads DATABASE_URL
    # from the environment/.env exactly as the app would — this is only to
    # learn the base URL to derive from, not the one that ends up used.
    base_url = os.environ.get("DATABASE_URL") or Settings().database_url
    parts = urlsplit(base_url)
    base_name = parts.path.lstrip("/") or "graphforge"
    test_name = base_name if base_name.endswith("_test") else f"{base_name}_test"
    return urlunsplit(parts._replace(path=f"/{test_name}"))


async def _ensure_database_exists(test_url: str) -> None:
    """`CREATE DATABASE` via asyncpg directly (SQLAlchemy's engine can't:
    it's already bound to a specific database, and Postgres has no
    `CREATE DATABASE IF NOT EXISTS`) — connecting to the driver-neutral
    `postgres` maintenance database, present on every Postgres server,
    to issue it. Racing another process doing the same (e.g. a parallel
    CI job) is fine: DuplicateDatabaseError just means it's already there.
    """
    parts = urlsplit(test_url)
    dbname = parts.path.lstrip("/")
    admin_dsn = urlunsplit(parts._replace(path="/postgres"))
    # asyncpg wants a plain `postgresql://` DSN, not SQLAlchemy's
    # `postgresql+asyncpg://`.
    admin_dsn = admin_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'CREATE DATABASE "{dbname}"')
    except asyncpg.DuplicateDatabaseError:
        pass
    finally:
        await conn.close()


def _run_migrations(test_url: str) -> None:
    """`Base.metadata.create_all` (the `client`/`db_session` fixtures' own
    schema setup, below) only creates what's declared on the ORM models —
    it silently skips anything that exists solely as a raw migration
    operation, e.g. `44c79114ee64_add_llm_invocations_table`'s composite
    `ix_llm_invocations_run_id_started_at` index, which has no matching
    `Index(...)` on the `LLMInvocation` model. Against the persistent dev
    database that gap never showed up, since some past `alembic upgrade
    head` had already applied it once and it just stayed. A fresh
    `<name>_test` database has no such history, so it needs the real
    migrations run against it too, not just `create_all`.
    """
    alembic_cfg = AlembicConfig(str(Path(__file__).parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(Path(__file__).parent.parent / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", test_url)
    alembic_command.upgrade(alembic_cfg, "head")


_TEST_DATABASE_URL = _test_database_url()
# Must happen before *any* `get_settings()` call in this process — including
# indirect ones — because it's `@lru_cache`d: whichever `DATABASE_URL` is in
# `os.environ` the first time it's called wins for the rest of the process,
# no matter what `os.environ` says afterward. `_run_migrations` below is
# exactly such an indirect call: Alembic's `env.py` does `get_settings()` of
# its own to build its config (confirmed the hard way — with this line placed
# after `_run_migrations` instead, that call cached the real dev DB's URL
# before this override ever ran, and every "isolated" test DB write actually
# landed back in the dev database). Set this first, before touching anything
# that might import `app.*`, and don't reorder it below `_run_migrations` or
# the imports further down — an import-sorter/E402 autofix that hoists those
# imports back above this line would silently reintroduce the same bug.
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
asyncio.run(_ensure_database_exists(_TEST_DATABASE_URL))
_run_migrations(_TEST_DATABASE_URL)

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.database.session import AsyncSessionLocal, engine, get_db_session  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.background_job import BackgroundJob  # noqa: E402
from app.orchestrator.worker import Worker  # noqa: E402


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
