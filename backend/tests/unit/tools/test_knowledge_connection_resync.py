"""Regression tests for `resync_knowledge_connections_for_source`.

The Tool Registry (`app.tools.registry.ToolRegistry`) holds exactly one live
instance per `tool_id`, but a source type like Jira supports multiple
`KnowledgeConnection` rows. Before this fix, only *creating* or *updating* a
connection pushed its config into the registry — deleting one, or disabling
one, left the registry silently serving a connection that no longer existed
or was turned off, until the process happened to restart (the only thing
that ever re-read the table from scratch). See
`app.tools.setup.resync_knowledge_connections_for_source`.

These tests run against the project's real database via the `db_session`
fixture (rolled back at teardown), which may already hold real Knowledge
Connections from actual use. They never assume the table is empty — only
that a row *this test created and then deleted or disabled* stops being the
one the registry serves. Test rows are timestamped far in the future so they
deterministically win the "most recently updated" comparison against any
pre-existing real data without needing to touch it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.models.knowledge_connection import KnowledgeConnection
from app.tools.registry import get_tool_registry
from app.tools.setup import register_all_tools, resync_knowledge_connections_for_source

pytestmark = pytest.mark.asyncio

_FAR_FUTURE = datetime.now(UTC) + timedelta(days=3650)


def _jira_row(
    *, base_url: str, updated_at: datetime = _FAR_FUTURE, enabled: bool = True
) -> KnowledgeConnection:
    return KnowledgeConnection(
        source_type="jira",
        name="Jira",
        transport="rest",
        auth_method="api_token",
        config={"base_url": base_url},
        encrypted_credentials=encrypt_secret(
            json.dumps({"email": "a@b.com", "api_token": "secret"})
        ),
        scope={},
        enabled=enabled,
        updated_at=updated_at,
    )


async def test_deleting_a_connection_stops_it_being_served(db_session: AsyncSession) -> None:
    """Regression: deleting a connection used to leave its credentials live
    in the registry forever — this is exactly the bug report ("even after
    deletion I am still able to find jira key from old connection")."""
    register_all_tools()

    row = _jira_row(base_url="https://old.atlassian.net")
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    await resync_knowledge_connections_for_source(db_session, "jira")
    tool = get_tool_registry().get_tool("jira")
    assert tool is not None
    assert tool._base_url == "https://old.atlassian.net"  # noqa: SLF001

    await db_session.delete(row)
    await db_session.commit()
    await resync_knowledge_connections_for_source(db_session, "jira")

    tool = get_tool_registry().get_tool("jira")
    assert tool is None or tool._base_url != "https://old.atlassian.net", (  # noqa: SLF001
        "a deleted connection's credentials must not keep answering lookups"
    )


async def test_deleting_the_newest_connection_falls_back_to_an_older_one(
    db_session: AsyncSession,
) -> None:
    """Regression: with an old and a new connection both present, adding the
    new one should make it win — but deleting it afterwards must fall back
    to the still-enabled old one, not keep serving the deleted connection's
    stale in-memory instance."""
    register_all_tools()

    older = _jira_row(
        base_url="https://old.atlassian.net", updated_at=_FAR_FUTURE - timedelta(minutes=10)
    )
    newer = _jira_row(base_url="https://new.atlassian.net", updated_at=_FAR_FUTURE)
    db_session.add_all([older, newer])
    await db_session.commit()
    await db_session.refresh(older)
    await db_session.refresh(newer)

    await resync_knowledge_connections_for_source(db_session, "jira")
    tool = get_tool_registry().get_tool("jira")
    assert tool is not None
    assert tool._base_url == "https://new.atlassian.net", (  # noqa: SLF001
        "the most recently updated enabled connection must win"
    )

    await db_session.delete(newer)
    await db_session.commit()
    await resync_knowledge_connections_for_source(db_session, "jira")

    tool = get_tool_registry().get_tool("jira")
    assert tool is not None
    assert tool._base_url == "https://old.atlassian.net", (  # noqa: SLF001
        "an older, still-enabled connection must take over once the newer one is deleted"
    )


async def test_disabling_a_connection_stops_it_being_served(db_session: AsyncSession) -> None:
    """Companion bug in the same code path: `update_connection` used to call
    `sync_knowledge_connection_to_tool` directly, which hardcodes
    `enabled=True` — so flipping a connection's `enabled` flag off in the UI
    never actually disabled the tool. Resyncing from the DB (which honors
    the `enabled` column in its query) fixes this too."""
    register_all_tools()

    row = _jira_row(base_url="https://toggle-me.atlassian.net")
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    await resync_knowledge_connections_for_source(db_session, "jira")
    tool = get_tool_registry().get_tool("jira")
    assert tool is not None
    assert tool._base_url == "https://toggle-me.atlassian.net"  # noqa: SLF001

    row.enabled = False
    await db_session.commit()
    await resync_knowledge_connections_for_source(db_session, "jira")

    tool = get_tool_registry().get_tool("jira")
    assert tool is None or tool._base_url != "https://toggle-me.atlassian.net", (  # noqa: SLF001
        "a disabled connection's credentials must not keep answering lookups"
    )
