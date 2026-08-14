"""Generic MCP (Model Context Protocol) client support.

One shared function every MCP-backed ITool implementation calls: connect to
a remote MCP server over Streamable HTTP, call one named tool, and return
its result as a plain dict. Tool-specific implementations (JiraTool, and
future GitHub/Confluence ones) own their own tool-name/argument mapping and
interpretation of the result — this module only owns the wire protocol, so
adding the next MCP-backed tool never means writing MCP plumbing again.

This is the concrete seam the Tool Registry was designed around: an agent
calls a tool by id and gets a ToolResult back, never knowing (or needing to
know) whether that tool's `execute()` made a REST call or went over MCP —
see app/tools/interfaces.py's ITool protocol.
"""

from __future__ import annotations

from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class MCPToolError(Exception):
    """Raised when an MCP tool call fails or the server reports an error."""


async def call_mcp_tool(
    server_url: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    auth_token: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Connect to `server_url`, call `tool_name(arguments)`, and return its
    structured result as a plain dict.

    Opens a fresh connection per call rather than pooling a persistent
    session — these tools are called a handful of times per agent run, not
    in a hot loop, so the simplicity of connect-call-disconnect is worth
    more here than the latency a kept-alive session would save.

    Raises MCPToolError on any transport failure or a server-reported tool
    error — callers (each ITool.execute()) are expected to catch this and
    translate it into a ToolResult(success=False, error=...), the same
    place any other tool's failure (a REST 404, a timeout) is handled.
    """
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None

    try:
        async with (
            streamablehttp_client(server_url, headers=headers, timeout=timeout) as (
                read_stream,
                write_stream,
                _get_session_id,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
    except MCPToolError:
        raise
    except Exception as exc:
        raise MCPToolError(f"MCP call to '{tool_name}' at {server_url} failed: {exc}") from exc

    if result.isError:
        raise MCPToolError(_extract_text(result) or f"MCP tool '{tool_name}' reported an error.")

    if result.structuredContent is not None:
        return dict(result.structuredContent)

    # Fallback for servers that only return unstructured text content —
    # callers get {"text": "..."} and parse it themselves if needed.
    return {"text": _extract_text(result)}


def _extract_text(result: Any) -> str:
    """Join every text-bearing content block in `result.content`, in
    order — the same "some servers only return unstructured content"
    fallback `call_mcp_tool` documents above.

    RFC-0035: a block carries its text one of two ways depending on MCP
    content-block type, and both are handled here, not just the first:
    - `TextContent`: text directly on the block (`block.text`) — this is
      the original, unchanged behavior.
    - `EmbeddedResource`: text one level down, on `block.resource.text`
      (a `TextResourceContents`). A real GitHub-hosted MCP server's
      `get_file_contents` was observed returning a `TextContent`
      confirmation message ("successfully downloaded text file...") and
      the actual file content as a *separate* `EmbeddedResource` block —
      silently dropped before this fix, since only `block.text` was ever
      read. `BlobResourceContents` (binary/base64 resources) has no
      `.text` attribute, so it's a no-op here, not a guess about content
      type — this stays duck-typed the same way `block.text` already was.
    """
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not text:
            resource = getattr(block, "resource", None)
            text = getattr(resource, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)
