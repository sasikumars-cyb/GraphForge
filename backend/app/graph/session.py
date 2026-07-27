"""Neo4j driver management — mirrors `app.database.session`'s pattern for
Postgres, but for the graph store.

Unlike a SQL session, a Neo4j driver is a connection pool meant to be
created once and shared — routers/services get it via `get_driver()`
rather than opening a new one per request.
"""

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.core.config import get_settings

_settings = get_settings()

_driver: AsyncDriver = AsyncGraphDatabase.driver(
    _settings.neo4j_uri,
    auth=(_settings.neo4j_user, _settings.neo4j_password),
)


def get_driver() -> AsyncDriver:
    return _driver


async def close_driver() -> None:
    """Close the driver's connection pool — called from app.main's lifespan
    shutdown. Without this, the pool's sockets stay open until the process
    exits rather than being released as part of a graceful shutdown."""
    await _driver.close()
