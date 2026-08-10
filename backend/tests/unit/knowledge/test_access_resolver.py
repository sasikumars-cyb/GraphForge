"""Tests for `app.knowledge.access_resolver` — the single implementation of
"how does GraphForge currently reach a knowledge source for a given
capability", replacing three previously-independent, disagreeing answers
to that question (the Tool Registry's own translation, the now-removed
`get_confluence_mcp_config`, and the now-removed `ConfluenceTool`'s own
REST/MCP fallback). See the module's own docstring for the full history.

`derive_access_methods` is pure — most of the coverage lives here, against
plain dicts, no DB. `resolve_knowledge_access` (the async, DB-backed
entrypoint `ConfluenceProvider` calls) gets its own thin coverage against
the real test database, mirroring `test_knowledge_connection_resync.py`'s
pattern.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.context_pipeline.models import ProviderCapability
from app.core.crypto import encrypt_secret
from app.knowledge.access_resolver import derive_access_methods, resolve_knowledge_access
from app.knowledge.registry import Transport
from app.models.knowledge_connection import KnowledgeConnection

_FAR_FUTURE = datetime.now(UTC) + timedelta(days=3650)


# ---------------------------------------------------------------------------
# derive_access_methods — pure, no I/O
# ---------------------------------------------------------------------------


def test_rest_connection_yields_a_rest_method_for_a_rest_capability() -> None:
    methods = derive_access_methods(
        "jira",
        "rest",
        ProviderCapability.ISSUE_TRACKER,
        {"base_url": "https://x.atlassian.net"},
        {"email": "a@b.com", "api_token": "secret"},
    )
    assert len(methods) >= 1
    rest = next(m for m in methods if m.transport == Transport.REST)
    assert rest.fields["base_url"] == "https://x.atlassian.net"
    assert rest.synthesized is False


def test_rest_connection_auto_wires_a_synthesized_mcp_method() -> None:
    """The core regression this module exists to fix: a REST-transport
    Confluence connection (exactly what Settings -> Integrations always
    creates — see TransportSpec.known_mcp_endpoint's own docstring) must
    still yield a usable MCP AccessMethod for DOCUMENTATION, reusing the
    REST api_token as the MCP bearer token against the known hosted
    endpoint — this is what makes Confluence document discovery work for
    a connection the UI actually produces, instead of only for one
    hand-edited directly in the database.

    A REST AccessMethod for DOCUMENTATION is also now present (see
    `app.agents.planning.confluence_rest_search` — the MCP-then-REST
    fallback `ConfluenceProvider` uses when Atlassian's org-level "API
    token access" toggle blocks MCP) — MCP still sorts first, matching
    JiraTool's own MCP-preferred, REST-fallback ordering."""
    methods = derive_access_methods(
        "confluence",
        "rest",
        ProviderCapability.DOCUMENTATION,
        {"base_url": "https://x.atlassian.net"},
        {"email": "a@b.com", "api_token": "secret-token"},
    )
    from app.core.config import get_settings

    if not get_settings().confluence_mcp_default_server_url:
        pytest.skip("no known_mcp_endpoint configured in this environment")

    assert len(methods) == 2
    mcp, rest = methods
    assert mcp.transport == Transport.MCP
    assert mcp.synthesized is True
    assert mcp.fields["api_key"] == "secret-token"
    assert mcp.fields["server_url"] == get_settings().confluence_mcp_default_server_url
    assert rest.transport == Transport.REST
    assert rest.synthesized is False
    assert rest.fields["api_token"] == "secret-token"


def test_rest_connection_yields_a_documentation_method_for_the_rest_fallback() -> None:
    """Confluence's REST transport now declares DOCUMENTATION (see
    `app.agents.planning.confluence_rest_search` — CQL search, the
    fallback `ConfluenceProvider.resolve_for_issue` uses when MCP is
    unreachable) — unlike before, the "own transport" branch does
    contribute a REST AccessMethod here, alongside the synthesized MCP
    one."""
    methods = derive_access_methods(
        "confluence",
        "rest",
        ProviderCapability.DOCUMENTATION,
        {"base_url": "https://x.atlassian.net"},
        {"email": "a@b.com", "api_token": "secret-token"},
    )
    assert any(m.transport == Transport.REST for m in methods)


def test_incomplete_rest_connection_yields_nothing_and_does_not_auto_wire() -> None:
    """Matches the pre-existing behavior this replaces exactly: an
    incomplete REST connection (missing `email`) must not produce a REST
    method, and must not auto-wire an MCP method either — auto-wire only
    ever fires once the REST connection's own fields already proved
    complete."""
    methods = derive_access_methods(
        "jira",
        "rest",
        ProviderCapability.ISSUE_TRACKER,
        {"base_url": "https://x.atlassian.net"},
        {"api_token": "secret"},  # missing "email"
    )
    assert methods == ()


def test_mcp_preferred_over_rest_when_both_available() -> None:
    """Matches JiraTool's existing runtime preference (MCP tried first,
    REST as fallback) — the resolver's ordering must agree, not leave it
    to each caller to re-sort."""
    methods = derive_access_methods(
        "jira",
        "rest",
        ProviderCapability.ISSUE_TRACKER,
        {"base_url": "https://x.atlassian.net"},
        {"email": "a@b.com", "api_token": "secret"},
    )
    from app.core.config import get_settings

    if not get_settings().jira_mcp_default_server_url:
        pytest.skip("no known_mcp_endpoint configured in this environment")
    assert methods[0].transport == Transport.MCP


def test_directly_configured_mcp_connection_is_not_synthesized() -> None:
    """A connection actually stored with transport="mcp" (server_url +
    api_key entered directly, not auto-wired from REST) must be reported
    as a real, non-synthesized method."""
    methods = derive_access_methods(
        "confluence",
        "mcp",
        ProviderCapability.DOCUMENTATION,
        {"server_url": "https://mcp.example/custom"},
        {"api_key": "key123"},
    )
    assert len(methods) == 1
    assert methods[0].synthesized is False
    assert methods[0].fields["server_url"] == "https://mcp.example/custom"


def test_capability_none_returns_every_method_the_tool_registry_needs() -> None:
    """The Tool Registry sync path (`_build_tool_config`) wants every
    method regardless of capability — it configures an ITool, not one
    capability. `capability=None` must not filter anything out."""
    methods = derive_access_methods(
        "jira",
        "rest",
        None,
        {"base_url": "https://x.atlassian.net"},
        {"email": "a@b.com", "api_token": "secret"},
    )
    assert any(m.transport == Transport.REST for m in methods)


def test_unknown_source_returns_nothing() -> None:
    assert derive_access_methods("not_a_real_source", "rest", None, {}, {}) == ()


# ---------------------------------------------------------------------------
# resolve_knowledge_access — async, DB-backed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_knowledge_access_no_connection_is_unavailable(
    db_session: AsyncSession,
) -> None:
    access = await resolve_knowledge_access(
        db_session, "not_a_real_source_type_xyz", ProviderCapability.DOCUMENTATION
    )
    assert access.available is False
    assert access.preferred() is None


@pytest.mark.asyncio
async def test_resolve_knowledge_access_finds_confluence_via_rest_auto_wire(
    db_session: AsyncSession,
) -> None:
    """End-to-end regression for the actual production bug: a Confluence
    Knowledge Connection created the way the UI actually creates one
    (transport="rest", email+api_token) must resolve to usable MCP access
    for the DOCUMENTATION capability — the exact case that silently never
    worked before this module existed."""
    from sqlalchemy import select

    from app.core.config import get_settings

    if not get_settings().confluence_mcp_default_server_url:
        pytest.skip("no known_mcp_endpoint configured in this environment")

    # This runs against the project's real (rolled-back-at-teardown)
    # database, which may already hold a real Confluence connection from
    # actual use — disable any so this test's own row is unambiguously the
    # one `resolve_knowledge_access` selects, same isolation concern
    # `test_knowledge_connection_resync.py` documents.
    existing = (
        (
            await db_session.execute(
                select(KnowledgeConnection).where(KnowledgeConnection.source_type == "confluence")
            )
        )
        .scalars()
        .all()
    )
    for other in existing:
        other.enabled = False
    await db_session.commit()

    row = KnowledgeConnection(
        source_type="confluence",
        name="Test Confluence",
        transport="rest",
        auth_method="basic",
        config={"base_url": "https://resolver-test.atlassian.net"},
        encrypted_credentials=encrypt_secret(
            json.dumps({"email": "a@b.com", "api_token": "resolver-secret"})
        ),
        scope={},
        enabled=True,
        updated_at=_FAR_FUTURE,
    )
    db_session.add(row)
    await db_session.commit()

    access = await resolve_knowledge_access(
        db_session, "confluence", ProviderCapability.DOCUMENTATION
    )

    assert access.available is True
    method = access.preferred()
    assert method.transport == Transport.MCP
    assert method.synthesized is True
    assert method.fields["api_key"] == "resolver-secret"
    assert access.config["base_url"] == "https://resolver-test.atlassian.net"
