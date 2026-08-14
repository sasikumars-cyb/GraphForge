"""Tests for app.tools.mcp_support._extract_text (RFC-0035).

Regression anchor: a real GitHub-hosted MCP server's `get_file_contents`
was observed returning file content as two content blocks — a
`TextContent` confirmation message ("successfully downloaded text
file...") and the actual source as a separate `EmbeddedResource` — and
`_extract_text` silently dropped the second block, so `source_file.text`
ended up holding only the confirmation message, never real code.

Fixtures use the real `mcp.types` classes (not approximate mocks), so a
change to the MCP SDK's own block shapes would surface here rather than
only in production.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import BlobResourceContents, EmbeddedResource, TextContent, TextResourceContents

from app.tools.mcp_support import _extract_text, call_mcp_tool


def _text_content(text: str) -> TextContent:
    return TextContent(type="text", text=text)


def _embedded_resource(text: str, uri: str = "repo://example/file.py") -> EmbeddedResource:
    return EmbeddedResource(
        type="resource",
        resource=TextResourceContents(uri=uri, mimeType="text/plain", text=text),
    )


def _result(*blocks: object) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks))


def test_text_content_only_behaves_exactly_as_before():
    result = _result(_text_content("hello"), _text_content("world"))
    assert _extract_text(result) == "hello\nworld"


def test_text_content_plus_embedded_resource_returns_the_actual_resource_text():
    result = _result(
        _text_content("successfully downloaded text file (SHA: abc123)"),
        _embedded_resource("def transform_meter():\n    return None\n"),
    )
    text = _extract_text(result)
    assert "successfully downloaded text file" in text
    assert "def transform_meter" in text


def test_embedded_resource_only_returns_the_resource_text():
    result = _result(_embedded_resource("print('actual source')"))
    assert _extract_text(result) == "print('actual source')"


def test_multiple_embedded_resources_are_combined_like_multiple_text_blocks():
    result = _result(
        _embedded_resource("first file content"),
        _embedded_resource("second file content"),
    )
    assert _extract_text(result) == "first file content\nsecond file content"


def test_mixed_text_content_and_multiple_embedded_resources_preserve_order():
    result = _result(
        _text_content("status: ok"),
        _embedded_resource("resource one"),
        _text_content("another status line"),
        _embedded_resource("resource two"),
    )
    assert _extract_text(result) == "status: ok\nresource one\nanother status line\nresource two"


def test_blob_resource_contents_has_no_text_and_is_silently_skipped():
    """A binary/base64 resource block must not raise or fabricate text —
    this is duck-typed the same way TextContent.text already was, never a
    file-extension or content-type guess."""
    blob_resource = EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(
            uri="repo://example/image.png", mimeType="image/png", blob="not-real-base64-data"
        ),
    )
    result = _result(_text_content("status message"), blob_resource)
    assert _extract_text(result) == "status message"


def test_empty_resource_text_is_treated_like_no_text():
    result = _result(_embedded_resource(""))
    assert _extract_text(result) == ""


def test_no_content_blocks_returns_empty_string():
    assert _extract_text(_result()) == ""


def test_result_with_no_content_attribute_at_all_returns_empty_string():
    assert _extract_text(SimpleNamespace()) == ""


# ---------------------------------------------------------------------------
# call_mcp_tool — the structuredContent path bypasses _extract_text entirely
# (untouched by this RFC); confirms it still does.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_content_path_is_unaffected_by_the_extract_text_change():
    fake_result = SimpleNamespace(
        isError=False,
        structuredContent={"path": "src/x.py", "content": "print(1)"},
        content=[_text_content("ignored — structuredContent wins")],
    )
    session = AsyncMock()
    session.initialize = AsyncMock()
    session.call_tool = AsyncMock(return_value=fake_result)

    @asynccontextmanager
    async def fake_streamablehttp_client(*_args, **_kwargs):
        yield (AsyncMock(), AsyncMock(), AsyncMock())

    @asynccontextmanager
    async def fake_client_session(*_args, **_kwargs):
        yield session

    with (
        patch("app.tools.mcp_support.streamablehttp_client", fake_streamablehttp_client),
        patch("app.tools.mcp_support.ClientSession", fake_client_session),
    ):
        payload = await call_mcp_tool("https://example.test/mcp", "get_file_contents", {})

    assert payload == {"path": "src/x.py", "content": "print(1)"}
