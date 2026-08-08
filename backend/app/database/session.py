"""Async SQLAlchemy engine and session factory.

Async (asyncpg) is used throughout so the DB layer never blocks FastAPI's
event loop.
"""

import json
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()


def _json_default(value: Any) -> Any:
    """Last-resort coercion for a JSON/JSONB column write — the persistence
    boundary's own backstop against a non-JSON-serializable Python value
    reaching `json.dumps`, independent of whatever produced it.

    This is deliberately NOT where the real fix for any specific bug
    belongs — the actual fix is never putting a `set` (or similar) into
    persisted state in the first place (see e.g. `context_pipeline.
    reasoning.engine._apply_memory_priority_boost`, which used to do
    exactly that). This function exists so that the *next* accidental
    `set`/`frozenset`/`bytes`/etc. degrades to a lossy-but-valid write
    instead of a hard crash that leaves a Run permanently stuck — commit
    failures still need their own handling (see `RunCoordinator.
    _commit_with_hook`) for the transaction-state side of that guarantee;
    this only prevents the serialization error from being the trigger.
    """
    if isinstance(value, set | frozenset):
        try:
            return sorted(value)
        except TypeError:
            return list(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    # Anything else genuinely unknown (a custom object with no JSON
    # representation) — string-ify rather than raise. A degraded value
    # persisted is still recoverable by a human reading it; a crashed
    # write is not.
    return str(value)


def _json_serializer(value: Any) -> str:
    return json.dumps(value, default=_json_default)


engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    json_serializer=_json_serializer,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a request-scoped DB session."""
    async with AsyncSessionLocal() as session:
        yield session
