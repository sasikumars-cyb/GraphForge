"""Unit tests for JiraTool's dual REST/MCP transport selection.

These don't hit real Jira or a real MCP server — httpx and call_mcp_tool
are mocked. The point is to verify: (1) config alone decides which
transport runs, (2) both transports produce the same ToolResult shape, so
nothing downstream needs to know which one ran.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.tools.implementations.jira_tool import JiraTool, extract_issue_key
from app.tools.interfaces import ToolInput
from app.tools.mcp_support import MCPToolError


def test_extract_issue_key_from_bare_key():
    assert extract_issue_key("Create Plan for NPT-6") == "NPT-6"


def test_extract_issue_key_from_url():
    text = "Create Plan for this https://cybage-team.atlassian.net/browse/NPT-6"
    assert extract_issue_key(text) == "NPT-6"


def test_extract_issue_key_none_found():
    assert extract_issue_key("Add schema evolution support") is None


def test_uses_mcp_when_mcp_server_url_present():
    tool = JiraTool({"jira_mcp_server_url": "https://example.com/mcp"})
    assert tool._uses_mcp is True


def test_uses_rest_when_only_rest_fields_present():
    tool = JiraTool(
        {
            "jira_base_url": "https://example.atlassian.net",
            "jira_email": "a@b.com",
            "jira_api_token": "token",
        }
    )
    assert tool._uses_mcp is False


@pytest.mark.asyncio
async def test_execute_not_configured():
    tool = JiraTool({})
    result = await tool.execute(ToolInput(query="NPT-6"))
    assert result.success is False
    assert "not configured" in result.error


@pytest.mark.asyncio
async def test_execute_no_issue_key_in_query():
    tool = JiraTool({"jira_mcp_server_url": "https://example.com/mcp"})
    result = await tool.execute(ToolInput(query="Add schema evolution support"))
    assert result.success is False
    assert "No Jira issue key" in result.error


@pytest.mark.asyncio
async def test_execute_via_mcp_success():
    tool = JiraTool(
        {
            "jira_mcp_server_url": "https://example.com/mcp",
            "jira_mcp_api_key": "secret",
        }
    )

    fake_payload = {
        "fields": {
            "summary": "Handle automatic schema evolution",
            "description": "Enable Delta Lake mergeSchema. Repo: ingestion-framework, etl-core",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Story"},
            "priority": {"name": "High"},
            "labels": ["data-platform"],
        },
        "url": "https://example.com/browse/NPT-6",
    }

    with patch(
        "app.tools.implementations.jira_tool.call_mcp_tool",
        new=AsyncMock(return_value=fake_payload),
    ) as mock_call:
        result = await tool.execute(ToolInput(query="Plan for NPT-6"))

    assert result.success is True
    assert result.data["issue_key"] == "NPT-6"
    assert result.data["summary"] == "Handle automatic schema evolution"
    assert "Enable Delta Lake mergeSchema" in result.data["description"]
    assert result.data["status"] == "In Progress"
    mock_call.assert_awaited_once()
    called_args = mock_call.await_args
    assert called_args.args[0] == "https://example.com/mcp"
    assert called_args.args[1] == "getJiraIssue"
    assert called_args.args[2] == {"issueIdOrKey": "NPT-6"}


@pytest.mark.asyncio
async def test_execute_via_mcp_failure():
    tool = JiraTool({"jira_mcp_server_url": "https://example.com/mcp"})

    with patch(
        "app.tools.implementations.jira_tool.call_mcp_tool",
        new=AsyncMock(side_effect=MCPToolError("connection refused")),
    ):
        result = await tool.execute(ToolInput(query="NPT-6"))

    assert result.success is False
    assert "connection refused" in result.error


@pytest.mark.asyncio
async def test_execute_via_rest_success():
    tool = JiraTool(
        {
            "jira_base_url": "https://example.atlassian.net",
            "jira_email": "a@b.com",
            "jira_api_token": "token",
        }
    )

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "fields": {
                    "summary": "Handle automatic schema evolution",
                    "description": None,
                    "status": {"name": "To Do"},
                    "issuetype": {"name": "Story"},
                    "priority": {"name": "Medium"},
                    "labels": [],
                }
            }

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse())):
        result = await tool.execute(ToolInput(query="NPT-6"))

    assert result.success is True
    assert result.data["summary"] == "Handle automatic schema evolution"
    assert result.data["status"] == "To Do"


@pytest.mark.asyncio
async def test_execute_via_rest_and_mcp_return_same_shape():
    """The Planning Agent reads result.data['context_text'] regardless of
    transport — both paths must populate the same keys."""
    rest_tool = JiraTool(
        {
            "jira_base_url": "https://example.atlassian.net",
            "jira_email": "a@b.com",
            "jira_api_token": "token",
        }
    )
    mcp_tool = JiraTool({"jira_mcp_server_url": "https://example.com/mcp"})

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "fields": {
                    "summary": "S",
                    "description": None,
                    "status": {"name": "To Do"},
                    "issuetype": {"name": "Story"},
                    "priority": {"name": "Medium"},
                    "labels": [],
                }
            }

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse())):
        rest_result = await rest_tool.execute(ToolInput(query="NPT-6"))

    with patch(
        "app.tools.implementations.jira_tool.call_mcp_tool",
        new=AsyncMock(
            return_value={
                "fields": {
                    "summary": "S",
                    "status": {"name": "To Do"},
                    "issuetype": {"name": "Story"},
                    "priority": {"name": "Medium"},
                },
            }
        ),
    ):
        mcp_result = await mcp_tool.execute(ToolInput(query="NPT-6"))

    assert set(rest_result.data.keys()) == set(mcp_result.data.keys())
