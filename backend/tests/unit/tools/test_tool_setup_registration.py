"""Unit tests for the metadata-driven Knowledge Connection → Tool Registry
sync (app.tools.setup), replacing the old hardcoded
_KNOWLEDGE_CONNECTION_TOOL_MAP / _MCP_AUTO_WIRE dicts.

These verify `_build_tool_config` derives everything from
`app.knowledge.registry.get_source(...).transports` — no source-specific
branching left in tools/setup.py itself (Part 2/3 of the MCP platform
refactor).
"""

from __future__ import annotations

import pytest

from app.tools.setup import _build_tool_config, build_tool_for_connection, register_all_tools


def test_no_hardcoded_registration_maps_remain() -> None:
    """The old (source_type, transport) -> (tool_id, field_map) dict and
    the separate auto-wire dict must be gone entirely — not just unused."""
    import app.tools.setup as setup_module

    assert not hasattr(setup_module, "_KNOWLEDGE_CONNECTION_TOOL_MAP")
    assert not hasattr(setup_module, "_MCP_AUTO_WIRE")
    assert not hasattr(setup_module, "_TOOL_FACTORIES")


def test_build_tool_config_jira_rest() -> None:
    tool_id, config = _build_tool_config(
        "jira",
        "rest",
        {"base_url": "https://example.atlassian.net"},
        {"email": "a@b.com", "api_token": "secret"},
    )
    assert tool_id == "jira"
    # Auto-wire (see test_auto_wire_reuses_rest_credential_as_mcp_bearer_token)
    # may add MCP keys on top of these three when a known_mcp_endpoint is
    # configured for this environment — assert the REST keys as a subset.
    assert config["jira_base_url"] == "https://example.atlassian.net"
    assert config["jira_email"] == "a@b.com"
    assert config["jira_api_token"] == "secret"


def test_build_tool_config_jira_mcp() -> None:
    tool_id, config = _build_tool_config(
        "jira",
        "mcp",
        {"server_url": "https://mcp.example.com"},
        {"api_key": "key123"},
    )
    assert tool_id == "jira"
    assert config == {
        "jira_mcp_server_url": "https://mcp.example.com",
        "jira_mcp_api_key": "key123",
    }


def test_build_tool_config_confluence_rest() -> None:
    tool_id, config = _build_tool_config(
        "confluence",
        "rest",
        {"base_url": "https://example.atlassian.net"},
        {"email": "a@b.com", "api_token": "secret"},
    )
    assert tool_id == "confluence"
    assert config["confluence_base_url"] == "https://example.atlassian.net"


def test_build_tool_config_incomplete_fields_returns_none() -> None:
    result = _build_tool_config(
        "jira", "rest", {"base_url": "https://example.atlassian.net"}, {}
    )  # missing email/api_token
    assert result is None


def test_build_tool_config_unknown_source_returns_none() -> None:
    assert _build_tool_config("not_a_real_source", "rest", {}, {}) is None


def test_build_tool_config_source_with_no_tool_id_returns_none() -> None:
    """neo4j/filesystem have no matching ITool — must be a clean no-op,
    not an exception."""
    assert _build_tool_config("neo4j", "database", {"uri": "bolt://x"}, {}) is None
    assert _build_tool_config("filesystem", "filesystem", {"root_path": "/x"}, {}) is None


def test_build_tool_config_github_returns_none() -> None:
    """GitHub is per-user OAuth (see tools/setup.py's github ToolSpec
    comment), never synced from a Knowledge Connection — its TransportSpecs
    correctly have no tool_id."""
    assert (
        _build_tool_config(
            "github", "rest", {"base_url": "https://api.github.com"}, {"token": "x"}
        )
        is None
    )


def test_auto_wire_reuses_rest_credential_as_mcp_bearer_token() -> None:
    """When a source's REST TransportSpec declares auto_wire_credential and
    its MCP TransportSpec has a known_mcp_endpoint, a REST-only connection
    should get MCP config keys populated too — reusing the REST token."""
    tool_id, config = _build_tool_config(
        "jira",
        "rest",
        {"base_url": "https://example.atlassian.net"},
        {"email": "a@b.com", "api_token": "secret-token"},
    )
    assert tool_id == "jira"
    # Only auto-wired if a known_mcp_endpoint is actually configured for
    # this environment (app.core.config's jira_mcp_default_server_url) —
    # assert the shape conditionally so this test doesn't depend on that
    # setting being present in the test environment.
    from app.knowledge.registry import Transport, get_source

    jira_spec = get_source("jira")
    mcp_spec = next(t for t in jira_spec.transports if t.transport == Transport.MCP)
    if mcp_spec.known_mcp_endpoint:
        assert config["jira_mcp_server_url"] == mcp_spec.known_mcp_endpoint
        assert config["jira_mcp_api_key"] == "secret-token"
    else:
        assert "jira_mcp_server_url" not in config


def test_build_tool_for_connection_uses_registered_tool_spec_factory() -> None:
    """build_tool_for_connection must construct its instance via the same
    ToolSpec.factory register_all_tools() already registered — no second
    tool_id -> constructor map."""
    register_all_tools()
    tool = build_tool_for_connection(
        "jira",
        "rest",
        {"base_url": "https://example.atlassian.net"},
        {"email": "a@b.com", "api_token": "secret"},
    )
    assert tool is not None
    assert tool.tool_id == "jira"


def test_build_tool_for_connection_unknown_source_returns_none() -> None:
    register_all_tools()
    assert build_tool_for_connection("neo4j", "database", {}, {}) is None


def test_transport_spec_carries_registration_metadata() -> None:
    """A new registry entry only needs its TransportSpec's tool_id /
    credential_field_map / auto_wire_credential filled in — no code in
    tools/setup.py needs to change (Part 3's two-step extension promise)."""
    from app.knowledge.registry import get_source

    jira_spec = get_source("jira")
    rest = next(t for t in jira_spec.transports if t.transport == "rest")
    assert rest.tool_id == "jira"
    assert rest.credential_field_map == {
        "base_url": "jira_base_url",
        "email": "jira_email",
        "api_token": "jira_api_token",
    }
    assert rest.auto_wire_credential == "api_token"
