"""Integration test for the startup AI config snapshot load (see
app.ai.config.store.refresh, wired into app.main's lifespan).

Uses the rollback-based `db_session` fixture directly (no HTTP layer) — same
rationale as test_recover_orphaned_runs.py: this is a plain
read-then-refresh against real AIProviderConfig/AISettings rows, none of the
cross-connection concerns `client`-fixture tests exist to cover, and
`httpx.ASGITransport` never runs the app's real lifespan anyway (see
app.main's own lifespan docstring and tests/conftest.py's `client` fixture
docstring for that established convention). Everything here stays inside
`db_session`'s one uncommitted transaction — `store.refresh()` is handed
that same session, so it sees these writes without a commit, and the
fixture's rollback at teardown leaves no trace even against a shared,
non-empty dev database.

Regression coverage for: on a fresh process, `resolver.resolve()` reads
`store.current_snapshot()` directly and cannot itself await a DB load (it
must stay synchronous — see its own module docstring). Before app.main's
lifespan called `store.refresh()` at startup, that snapshot sat empty
(`loaded=False`, `default_provider=None`) from process start until
whichever `ai_workspace` request happened to call `ensure_loaded()` first —
which, for a run started by the embedded Worker before any such request,
could be never. Every AI call in that window silently used the
environment-tier provider instead of whatever was actually configured in
the UI, with no error, only a confusing downstream failure on a provider
nobody chose.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.config import store
from app.ai.config.resolver import resolve
from app.core.config import Settings
from app.core.crypto import encrypt_secret
from app.models.ai_provider_config import AIProviderConfig, AISettings

pytestmark = pytest.mark.asyncio

# Not one of the providers already configured in a shared dev database (see
# module docstring) — avoids colliding with the unique `provider_key`
# constraint on a row this test's rollback won't have removed yet.
_TEST_PROVIDER_KEY = "cerebras"


@pytest.fixture(autouse=True)
def _clean_snapshot():
    """Every test starts from — and leaves behind — an unloaded snapshot,
    so this module's manual `store.invalidate()`/`store.refresh()` calls
    never leak into whichever test runs next in the same process."""
    store.invalidate()
    yield
    store.invalidate()


def _env_only_settings() -> Settings:
    """The environment tier a fresh, unconfigured install would fall back
    to — deliberately a different provider than the one under test, so a
    test that resolves against the wrong tier is unambiguous."""
    return Settings(ai_provider="groq", groq_api_key="env-groq-key")


async def _point_ai_settings_at_test_provider(db_session: AsyncSession) -> None:
    """Set default_provider on the (singleton) AISettings row, creating it
    if this is the first thing in the suite to touch it. Mutates in place
    rather than inserting a second row — there is no unique constraint to
    collide with, but a second row would leave `select(AISettings).limit(1)`
    (store.refresh's own query, with no ORDER BY) picking whichever row the
    database feels like, which a real singleton table never has to
    consider."""
    existing = (await db_session.execute(select(AISettings).limit(1))).scalar_one_or_none()
    if existing is not None:
        existing.default_provider = _TEST_PROVIDER_KEY
    else:
        db_session.add(AISettings(id=uuid.uuid4(), default_provider=_TEST_PROVIDER_KEY))
    await db_session.flush()


async def test_resolve_falls_back_to_environment_before_snapshot_is_loaded(
    db_session: AsyncSession,
) -> None:
    """Documents the bug this startup load fixes: a stored default_provider
    in the database is invisible to resolve() until something loads the
    snapshot — an unloaded snapshot silently resolves to the environment
    tier even though the UI has a provider configured."""
    db_session.add(
        AIProviderConfig(
            id=uuid.uuid4(),
            provider_key=_TEST_PROVIDER_KEY,
            encrypted_api_key=encrypt_secret("stored-key"),
            model="llama-3.3-70b",
        )
    )
    await _point_ai_settings_at_test_provider(db_session)

    store.invalidate()  # simulates process start: nothing has loaded the snapshot yet

    resolved = resolve(settings=_env_only_settings())
    assert resolved.source == "environment"
    assert resolved.key == "groq"


async def test_resolve_honors_stored_default_after_startup_load(
    db_session: AsyncSession,
) -> None:
    """The fix: app.main's lifespan calls `store.refresh(db)` before the
    embedded Worker (or any request) can run an agent — this is that same
    call, proving it closes the gap above."""
    db_session.add(
        AIProviderConfig(
            id=uuid.uuid4(),
            provider_key=_TEST_PROVIDER_KEY,
            encrypted_api_key=encrypt_secret("stored-key"),
            model="llama-3.3-70b",
        )
    )
    await _point_ai_settings_at_test_provider(db_session)

    store.invalidate()
    await store.refresh(db_session)  # the call app.main's lifespan now makes at startup

    resolved = resolve(settings=_env_only_settings())
    assert resolved.source == "stored_default"
    assert resolved.key == _TEST_PROVIDER_KEY
    assert resolved.model == "llama-3.3-70b"
    assert resolved.config.api_key == "stored-key"
