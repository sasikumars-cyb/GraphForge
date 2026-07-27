"""Shared pytest fixtures."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import rate_limit
from app.database.base import Base
from app.database.session import engine, get_db_session
from app.main import create_app


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
    """A plain client against a fresh app instance, no database involved.

    Use this for anything that doesn't touch the DB (health, error handling)
    so those tests stay independent of Postgres being reachable at all.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


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
