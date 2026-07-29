"""Unit tests for ConfluenceTool's dual REST/MCP transport selection.

Same pattern as test_jira_tool.py: httpx and call_mcp_tool are mocked, no
real Confluence or MCP server is hit. Covers the REST CQL search path this
tool didn't have before (Part 5 of the MCP platform refactor) plus the
MCP-then-REST fallback policy (Part 6), mirroring Jira's existing tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.tools.implementations.confluence_tool import ConfluenceTool
from app.tools.interfaces import ToolInput
from app.tools.mcp_support import MCPToolError


def test_uses_mcp_when_mcp_server_url_present():
    tool = ConfluenceTool({"confluence_mcp_server_url": "https://example.com/mcp"})
    assert tool._uses_mcp is True


def test_uses_rest_when_only_rest_fields_present():
    tool = ConfluenceTool(
        {
            "confluence_base_url": "https://example.atlassian.net",
            "confluence_email": "a@b.com",
            "confluence_api_token": "token",
        }
    )
    assert tool._uses_mcp is False
    assert tool._uses_rest is True


@pytest.mark.asyncio
async def test_execute_not_configured():
    tool = ConfluenceTool({})
    result = await tool.execute(ToolInput(query="rate limiter design"))
    assert result.success is False
    assert "not configured" in result.error


@pytest.mark.asyncio
async def test_execute_via_rest_success():
    tool = ConfluenceTool(
        {
            "confluence_base_url": "https://example.atlassian.net",
            "confluence_email": "a@b.com",
            "confluence_api_token": "token",
        }
    )

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "ADR-012: Rate Limiter Design",
                        "excerpt": "We chose a token bucket algorithm...",
                        "url": "/spaces/ENG/pages/123/ADR-012",
                        "content": {"title": "ADR-012: Rate Limiter Design"},
                    }
                ]
            }

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse())):
        result = await tool.execute(ToolInput(query="rate limiter"))

    assert result.success is True
    assert len(result.data["pages"]) == 1
    page = result.data["pages"][0]
    assert page["title"] == "ADR-012: Rate Limiter Design"
    assert page["url"] == "https://example.atlassian.net/spaces/ENG/pages/123/ADR-012"
    assert "token bucket" in page["excerpt"]
    assert "ADR-012" in result.data["context_text"]


@pytest.mark.asyncio
async def test_execute_via_rest_empty_query():
    tool = ConfluenceTool(
        {
            "confluence_base_url": "https://example.atlassian.net",
            "confluence_email": "a@b.com",
            "confluence_api_token": "token",
        }
    )
    result = await tool.execute(ToolInput(query="   "))
    assert result.success is False
    assert "Empty" in result.error


@pytest.mark.asyncio
async def test_execute_via_rest_no_results():
    tool = ConfluenceTool(
        {
            "confluence_base_url": "https://example.atlassian.net",
            "confluence_email": "a@b.com",
            "confluence_api_token": "token",
        }
    )

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse())):
        result = await tool.execute(ToolInput(query="nonexistent topic"))

    assert result.success is True
    assert result.data["pages"] == []
    assert "No Confluence pages found" in result.data["context_text"]


@pytest.mark.asyncio
async def test_execute_via_rest_auth_failure():
    tool = ConfluenceTool(
        {
            "confluence_base_url": "https://example.atlassian.net",
            "confluence_email": "a@b.com",
            "confluence_api_token": "wrong-token",
        }
    )

    class FakeResponse:
        status_code = 401
        headers = {"content-type": "application/json"}
        text = "Unauthorized"

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse())):
        result = await tool.execute(ToolInput(query="rate limiter"))

    assert result.success is False
    assert "authentication failed" in result.error.lower()


@pytest.mark.asyncio
async def test_execute_via_mcp_success():
    tool = ConfluenceTool(
        {
            "confluence_mcp_server_url": "https://example.com/mcp",
            "confluence_mcp_api_key": "secret",
        }
    )

    fake_payload = {
        "results": [
            {"title": "Runbook: Payment Retry", "excerpt": "Steps to...", "url": "https://x/y"}
        ]
    }

    with patch(
        "app.tools.implementations.confluence_tool.call_mcp_tool",
        new=AsyncMock(return_value=fake_payload),
    ) as mock_call:
        result = await tool.execute(ToolInput(query="payment retry runbook"))

    mock_call.assert_awaited_once()
    assert result.success is True
    assert result.data["pages"][0]["title"] == "Runbook: Payment Retry"


@pytest.mark.asyncio
async def test_mcp_failure_falls_back_to_rest():
    """An auto-wired MCP endpoint (see app.tools.setup) may not actually be
    reachable/compatible — a recoverable MCP failure must fall back to
    REST when REST credentials are also present, exactly like JiraTool."""
    tool = ConfluenceTool(
        {
            "confluence_mcp_server_url": "https://example.com/mcp",
            "confluence_mcp_api_key": "secret",
            "confluence_base_url": "https://example.atlassian.net",
            "confluence_email": "a@b.com",
            "confluence_api_token": "token",
        }
    )

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"title": "Fallback result", "excerpt": "", "url": "/spaces/x"},
                ]
            }

    with (
        patch(
            "app.tools.implementations.confluence_tool.call_mcp_tool",
            new=AsyncMock(side_effect=MCPToolError("connection refused")),
        ),
        patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse())),
    ):
        result = await tool.execute(ToolInput(query="rate limiter"))

    assert result.success is True
    assert result.data["pages"][0]["title"] == "Fallback result"


@pytest.mark.asyncio
async def test_mcp_failure_without_rest_surfaces_as_failure():
    tool = ConfluenceTool({"confluence_mcp_server_url": "https://example.com/mcp"})

    with patch(
        "app.tools.implementations.confluence_tool.call_mcp_tool",
        new=AsyncMock(side_effect=MCPToolError("connection refused")),
    ):
        result = await tool.execute(ToolInput(query="rate limiter"))

    assert result.success is False
    assert "connection refused" in result.error


@pytest.mark.asyncio
async def test_execute_via_rest_and_mcp_return_same_shape():
    """The Planning Agent reads result.data['context_text'] regardless of
    transport — both paths must populate the same keys."""
    rest_tool = ConfluenceTool(
        {
            "confluence_base_url": "https://example.atlassian.net",
            "confluence_email": "a@b.com",
            "confluence_api_token": "token",
        }
    )
    mcp_tool = ConfluenceTool({"confluence_mcp_server_url": "https://example.com/mcp"})

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"title": "T", "excerpt": "E", "url": "/x"}]}

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse())):
        rest_result = await rest_tool.execute(ToolInput(query="q"))

    with patch(
        "app.tools.implementations.confluence_tool.call_mcp_tool",
        new=AsyncMock(return_value={"results": [{"title": "T", "excerpt": "E", "url": "https://x"}]}),
    ):
        mcp_result = await mcp_tool.execute(ToolInput(query="q"))

    assert set(rest_result.data.keys()) == set(mcp_result.data.keys())
    assert rest_result.success == mcp_result.success == True  # noqa: E712


@pytest.mark.asyncio
async def test_health_check_rest_unconfigured():
    tool = ConfluenceTool({})
    assert await tool.health_check() == "unconfigured"


@pytest.mark.asyncio
async def test_health_check_rest_auth_failed():
    tool = ConfluenceTool(
        {
            "confluence_base_url": "https://example.atlassian.net",
            "confluence_email": "a@b.com",
            "confluence_api_token": "bad",
        }
    )

    class FakeResponse:
        status_code = 401
        headers = {"content-type": "application/json"}

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse())):
        health = await tool.health_check()
    assert health == "auth_failed"


@pytest.mark.asyncio
async def test_health_check_rest_healthy():
    tool = ConfluenceTool(
        {
            "confluence_base_url": "https://example.atlassian.net",
            "confluence_email": "a@b.com",
            "confluence_api_token": "token",
        }
    )

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse())):
        health = await tool.health_check()
    assert health == "healthy"
