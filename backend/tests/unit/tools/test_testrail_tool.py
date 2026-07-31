"""Unit tests for TestRailTool — mainly the query string TestRail's API
actually needs, since a real bug shipped here undetected: `_get()` used to
build the URL via `httpx.AsyncClient.get(url, params=...)`, and httpx's own
merge of `params=` into a URL that already has a non-"key=value" query
fragment ("index.php?/api/v2/get_projects") silently drops that fragment -
so every real call landed on "index.php?limit=250&offset=0" and TestRail
404'd. No mocked test caught it because nothing asserted on the actual URL
requested, only on the (mocked) response - these tests do.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.tools.implementations.testrail_tool import TestRailApiError, TestRailTool
from app.tools.interfaces import ToolHealth

_CONFIG = {
    "testrail_base_url": "https://example.testrail.io",
    "testrail_email": "user@example.com",
    "testrail_api_token": "secret-key",
}


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, is_error=False, text=""):
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.is_error = is_error
        self.text = text
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


@pytest.mark.asyncio
async def test_get_projects_requests_the_actual_api_path_with_params():
    """Regression test for the dropped-path bug: the requested URL must
    contain both "/api/v2/get_projects" and the pagination params, not just
    the params alone."""
    tool = TestRailTool(_CONFIG)
    fake_response = FakeResponse(json_data={"projects": [], "_links": {"next": None}})

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)) as mock_get:
        await tool.list_projects()

    requested_url = mock_get.call_args[0][0]
    assert "/index.php?/api/v2/get_projects" in requested_url
    assert "limit=250" in requested_url
    assert "offset=0" in requested_url


@pytest.mark.asyncio
async def test_get_suites_requests_the_project_scoped_path():
    tool = TestRailTool(_CONFIG)
    fake_response = FakeResponse(json_data={"suites": []})

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)) as mock_get:
        await tool.list_suites(42)

    requested_url = mock_get.call_args[0][0]
    assert requested_url == "https://example.testrail.io/index.php?/api/v2/get_suites/42"


@pytest.mark.asyncio
async def test_health_check_healthy_when_projects_reachable():
    tool = TestRailTool(_CONFIG)
    fake_response = FakeResponse(json_data={"projects": [], "_links": {"next": None}})

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)):
        health = await tool.health_check()

    assert health == ToolHealth.HEALTHY


@pytest.mark.asyncio
async def test_health_check_auth_failed_on_401():
    tool = TestRailTool(_CONFIG)
    fake_response = FakeResponse(status_code=401, is_error=True, text="Unauthorized")

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)):
        health = await tool.health_check()

    assert health == ToolHealth.AUTH_FAILED


@pytest.mark.asyncio
async def test_health_check_offline_on_404():
    """A wrong base_url (or the dropped-path bug this file regression-tests)
    surfaces as a 404 from TestRail - not an auth failure."""
    tool = TestRailTool(_CONFIG)
    fake_response = FakeResponse(status_code=404, is_error=True, text="File Not Found")

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)):
        health = await tool.health_check()

    assert health == ToolHealth.OFFLINE


@pytest.mark.asyncio
async def test_health_check_unconfigured_when_fields_missing():
    tool = TestRailTool({"testrail_base_url": "https://example.testrail.io"})

    health = await tool.health_check()

    assert health == ToolHealth.UNCONFIGURED


@pytest.mark.asyncio
async def test_non_json_response_raises_a_helpful_error():
    """A wrong base_url pointing somewhere that returns an HTML page (e.g. a
    login redirect) must not be mistaken for a valid empty result."""
    tool = TestRailTool(_CONFIG)
    fake_response = FakeResponse(status_code=200, is_error=False)
    fake_response.headers = {"content-type": "text/html"}

    with (
        patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)),
        pytest.raises(TestRailApiError, match="non-JSON response"),
    ):
        await tool.list_projects()
