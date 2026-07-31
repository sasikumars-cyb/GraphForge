"""Unit tests for GitHubTool's dual REST/MCP transport selection, including
`get_repository`/`get_file_contents` — the broadened MCP surface added
alongside PAT connect support. Same `httpx.AsyncClient.get`/`call_mcp_tool`
patch convention as `test_jira_tool.py`; no real network calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.tools.implementations.github_tool import GitHubTool, extract_pr_or_issue_ref
from app.tools.interfaces import ToolInput
from app.tools.mcp_support import MCPToolError


def test_extract_pr_or_issue_ref_from_shorthand():
    assert extract_pr_or_issue_ref("Continue the work in acme/widgets#42") == (
        "acme",
        "widgets",
        42,
    )


def test_extract_pr_or_issue_ref_none_for_bare_repository():
    assert extract_pr_or_issue_ref("Look at acme/widgets for the pattern") is None


class FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text or ""

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json_data


# ---------------------------------------------------------------------------
# execute() — existing PR/issue path, unaffected by the new methods below
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_via_rest_still_fetches_a_pull_request():
    tool = GitHubTool({"github_token": "gho_faketoken"})

    with patch(
        "httpx.AsyncClient.get",
        new=AsyncMock(
            return_value=FakeResponse(
                200,
                {
                    "title": "Add retries",
                    "body": "Fixes flaky test",
                    "state": "open",
                    "pull_request": {},
                    "labels": [],
                    "html_url": "https://github.com/acme/widgets/pull/42",
                },
            )
        ),
    ):
        result = await tool.execute(ToolInput(query="acme/widgets#42"))

    assert result.success is True
    assert result.data["is_pull_request"] is True
    assert result.data["title"] == "Add retries"


# ---------------------------------------------------------------------------
# get_repository — new
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_repository_not_configured():
    tool = GitHubTool({})
    result = await tool.get_repository("acme", "widgets")
    assert result.success is False
    assert "not configured" in result.error


@pytest.mark.asyncio
async def test_get_repository_via_rest_success():
    tool = GitHubTool({"github_token": "gho_faketoken"})

    with patch(
        "httpx.AsyncClient.get",
        new=AsyncMock(
            return_value=FakeResponse(
                200,
                {
                    "description": "Widget factory",
                    "default_branch": "main",
                    "language": "Python",
                    "topics": ["widgets", "factory"],
                    "stargazers_count": 12,
                    "html_url": "https://github.com/acme/widgets",
                },
            )
        ),
    ):
        result = await tool.get_repository("acme", "widgets")

    assert result.success is True
    assert result.data["description"] == "Widget factory"
    assert result.data["default_branch"] == "main"
    assert result.data["topics"] == ["widgets", "factory"]
    assert "Widget factory" in result.data["context_text"]


@pytest.mark.asyncio
async def test_get_repository_via_rest_not_found():
    tool = GitHubTool({"github_token": "gho_faketoken"})

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse(404))):
        result = await tool.get_repository("acme", "ghost")

    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_get_repository_via_mcp_success():
    tool = GitHubTool({"github_mcp_server_url": "https://mcp.example/github"})

    fake_payload = {
        "description": "Widget factory",
        "defaultBranch": "main",
        "language": "Python",
        "topics": ["widgets"],
        "stars": 5,
        "url": "https://github.com/acme/widgets",
    }

    with patch(
        "app.tools.implementations.github_tool.call_mcp_tool",
        new=AsyncMock(return_value=fake_payload),
    ) as mock_call:
        result = await tool.get_repository("acme", "widgets")

    assert result.success is True
    assert result.data["default_branch"] == "main"
    assert result.data["stars"] == 5
    mock_call.assert_awaited_once()
    assert mock_call.await_args.args[1] == "get_repository"
    assert mock_call.await_args.args[2] == {"owner": "acme", "repo": "widgets"}


@pytest.mark.asyncio
async def test_get_repository_via_mcp_failure_falls_back_to_rest():
    tool = GitHubTool(
        {
            "github_token": "gho_faketoken",
            "github_mcp_server_url": "https://mcp.example/github",
        }
    )

    with (
        patch(
            "app.tools.implementations.github_tool.call_mcp_tool",
            new=AsyncMock(side_effect=MCPToolError("connection refused")),
        ),
        patch(
            "httpx.AsyncClient.get",
            new=AsyncMock(
                return_value=FakeResponse(
                    200,
                    {
                        "description": "Widget factory",
                        "default_branch": "main",
                        "language": "Python",
                        "topics": [],
                        "stargazers_count": 0,
                        "html_url": "https://github.com/acme/widgets",
                    },
                )
            ),
        ),
    ):
        result = await tool.get_repository("acme", "widgets")

    assert result.success is True
    assert result.data["description"] == "Widget factory"


# ---------------------------------------------------------------------------
# get_file_contents — new
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_contents_via_rest_success():
    tool = GitHubTool({"github_token": "gho_faketoken"})

    with patch(
        "httpx.AsyncClient.get",
        new=AsyncMock(return_value=FakeResponse(200, text="*.py @alice\n")),
    ):
        result = await tool.get_file_contents("acme", "widgets", "CODEOWNERS")

    assert result.success is True
    assert result.data["content"] == "*.py @alice\n"


@pytest.mark.asyncio
async def test_get_file_contents_via_rest_not_found():
    tool = GitHubTool({"github_token": "gho_faketoken"})

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=FakeResponse(404))):
        result = await tool.get_file_contents("acme", "widgets", "MISSING.md")

    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_get_file_contents_via_mcp_decodes_base64():
    import base64

    tool = GitHubTool({"github_mcp_server_url": "https://mcp.example/github"})
    encoded = base64.b64encode(b"print('hi')\n").decode("ascii")

    with patch(
        "app.tools.implementations.github_tool.call_mcp_tool",
        new=AsyncMock(return_value={"content": encoded, "encoding": "base64"}),
    ) as mock_call:
        result = await tool.get_file_contents("acme", "widgets", "main.py", ref="develop")

    assert result.success is True
    assert result.data["content"] == "print('hi')\n"
    assert mock_call.await_args.args[2] == {
        "owner": "acme",
        "repo": "widgets",
        "path": "main.py",
        "ref": "develop",
    }


@pytest.mark.asyncio
async def test_get_file_contents_via_mcp_plain_text_not_decoded():
    tool = GitHubTool({"github_mcp_server_url": "https://mcp.example/github"})

    with patch(
        "app.tools.implementations.github_tool.call_mcp_tool",
        new=AsyncMock(return_value={"content": "*.py @alice\n"}),
    ):
        result = await tool.get_file_contents("acme", "widgets", "CODEOWNERS")

    assert result.success is True
    assert result.data["content"] == "*.py @alice\n"


@pytest.mark.asyncio
async def test_get_file_contents_truncates_long_content():
    tool = GitHubTool({"github_token": "gho_faketoken"})
    long_content = "x" * 9000

    with patch(
        "httpx.AsyncClient.get",
        new=AsyncMock(return_value=FakeResponse(200, text=long_content)),
    ):
        result = await tool.get_file_contents("acme", "widgets", "big.txt")

    assert result.success is True
    assert result.data["truncated"] is True
    assert len(result.data["content"]) == 8000
