"""Confluence context via Atlassian's Teamwork Graph MCP tools.

Atlassian's official Confluence/Jira MCP server exposes no plain "search"
tool — only two graph-traversal primitives: `getTeamworkGraphContext`
(discover what's connected to a known entity) and `getTeamworkGraphObject`
(fetch full content for specific ARIs/URLs). There is no way to search
Confluence from a free-text query alone; every traversal has to start from
a concrete anchor entity — confirmed by querying the server's own
`tools/list` directly (see the session that built this module for the raw
schemas). This is also exactly how Claude/Copilot "search" through this
same server: the AI client's own model drives a discover-then-fetch loop,
not a single search call.

The one anchor GraphForge reliably has is the Jira issue a workflow
references (see planning/agent.py's Jira enrichment, which runs first and
yields the issue key this module needs). Given that anchor, this hands the
LLM itself both tools and lets it decide what's worth exploring and
fetching, rather than GraphForge hardcoding which relationship types or
how many hops to follow.

Bounded to a handful of turns (_MAX_TOOL_TURNS) so a model that keeps
asking for more context can't turn one planning run into an unbounded
number of paid LLM + MCP calls — the same cost-boundedness principle
behind the reflection pass and the rate limiter.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.agents.llm import STAGE_PLANNING, StageAwareLLMProvider
from app.ai.providers.base import ToolSpec, ToolTurnResult
from app.core.crypto import decrypt_secret
from app.core.exceptions import AppError
from app.models.knowledge_connection import KnowledgeConnection
from app.tools.mcp_support import MCPToolError, call_mcp_tool

logger = logging.getLogger(__name__)


async def get_confluence_mcp_config(db: AsyncSession) -> tuple[str, str, str] | None:
    """Look up the first enabled, MCP-transport Confluence Knowledge
    Connection and return (server_url, auth_token, cloud_id), or None if
    none is configured that way — the REST-only stub path (see
    ConfluenceTool's module docstring) can't be used for graph traversal.

    `cloud_id` uses the connection's `base_url` — Atlassian's own tool
    schema documents cloudId as accepting "a UUID or site URL" directly,
    so no separate cloud-ID resolution call is needed.
    """
    result = await db.execute(
        select(KnowledgeConnection).where(
            KnowledgeConnection.source_type == "confluence",
            KnowledgeConnection.transport == "mcp",
            KnowledgeConnection.enabled.is_(True),
        )
    )
    row = result.scalars().first()
    if row is None:
        return None

    server_url = (row.config or {}).get("server_url")
    cloud_id = (row.config or {}).get("base_url") or (row.config or {}).get("server_url")
    if not server_url or not cloud_id or not row.encrypted_credentials:
        return None

    try:
        credentials = json.loads(decrypt_secret(row.encrypted_credentials))
    except Exception:
        logger.warning("confluence_mcp_config_decrypt_failed connection_id=%s", row.id)
        return None

    auth_token = credentials.get("api_key", "")
    return server_url, auth_token, cloud_id


_MAX_TOOL_TURNS = 4

_SYSTEM_PROMPT = (
    "You are gathering context from a company's Confluence/Jira knowledge graph "
    "to ground an engineering plan. You have two tools: getTeamworkGraphContext "
    "(discover what's connected to a known entity — linked pages, docs, issues) "
    "and getTeamworkGraphObject (fetch full content for specific items you found "
    "via getTeamworkGraphContext). Start from the given anchor entity, follow "
    "only what looks relevant to the task, and stop once you have enough — do "
    "not over-explore or fetch things unrelated to the task. When you are done, "
    "respond with a plain-text summary (not JSON) of anything relevant you found "
    "(design docs, ADRs, related decisions) with enough detail to actually use, "
    "or say exactly 'No relevant Confluence content found' if nothing was."
)

_CONTEXT_TOOL = ToolSpec(
    name="getTeamworkGraphContext",
    description=(
        "Discover items connected to a known entity — linked Confluence pages, "
        "Jira issues, comments, etc. cloudId is filled in automatically; do not "
        "pass it yourself."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "objectType": {
                "type": "string",
                "enum": ["JiraWorkItem", "ConfluencePage", "ConfluenceSpace"],
            },
            "objectIdentifier": {
                "type": "string",
                "description": "Key, ID, ARI, or URL of the entity, e.g. 'ENG-123'.",
            },
            "targetObjectTypes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional filter, e.g. ['ConfluencePage'] to only see linked docs.",
            },
        },
        "required": ["objectType", "objectIdentifier"],
        "additionalProperties": False,
    },
)

_OBJECT_TOOL = ToolSpec(
    name="getTeamworkGraphObject",
    description=(
        "Fetch full content for specific items (ARIs or URLs) found via "
        "getTeamworkGraphContext. cloudId is filled in automatically; do not "
        "pass it yourself."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "objects": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 10,
            },
        },
        "required": ["objects"],
        "additionalProperties": False,
    },
)


def _summarize_mcp_result(result: dict[str, Any]) -> str:
    text = str(result)
    return text[:200] + ("…" if len(text) > 200 else "")


async def gather_confluence_context(
    *,
    mcp_server_url: str,
    mcp_auth_token: str,
    cloud_id: str,
    jira_issue_key: str,
    task_description: str,
    model: str | None,
    stage: str = STAGE_PLANNING,
) -> tuple[str | None, list[Evidence]]:
    """Runs a bounded, LLM-driven discover-then-fetch loop against
    Atlassian's Teamwork Graph MCP tools, anchored on `jira_issue_key`.

    Returns (summary_text, evidence). summary_text is None if the model
    found nothing relevant, the active provider doesn't support native
    tool-calling, or the LLM call itself failed — this is optional
    grounding, not a required step (same policy as Jira/GitHub
    enrichment in planning/agent.py), so it never raises.
    """
    try:
        provider = StageAwareLLMProvider(stage=stage, model=model)
        provider.preview()  # fail fast on misconfiguration, before any turn
    except AppError:
        return None, []

    evidence: list[Evidence] = []
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "text": (
                        f"Task: {task_description}\n\n"
                        f"Anchor entity: Jira work item {jira_issue_key} "
                        f"(objectType=JiraWorkItem, objectIdentifier={jira_issue_key})."
                    )
                }
            ],
        }
    ]

    for _turn in range(_MAX_TOOL_TURNS):
        try:
            result: ToolTurnResult = await provider.complete_with_tools(
                system_prompt=_SYSTEM_PROMPT,
                messages=messages,
                tools=[_CONTEXT_TOOL, _OBJECT_TOOL],
            )
        except NotImplementedError:
            logger.info("confluence_context_skipped_no_tool_calling_support")
            return None, []
        except AppError:
            logger.warning("confluence_context_llm_call_failed", exc_info=True)
            return None, evidence

        if not result.tool_uses:
            text = result.text.strip()
            if not text or text.lower().startswith("no relevant"):
                return None, evidence
            return text, evidence

        messages.append({"role": "assistant", "content": result.content_blocks})
        tool_result_blocks: list[dict[str, Any]] = []
        for call in result.tool_uses:
            args = {**call.input, "cloudId": cloud_id}
            try:
                mcp_result = await call_mcp_tool(
                    mcp_server_url, call.name, args, auth_token=mcp_auth_token, timeout=15.0
                )
                evidence.append(
                    Evidence(
                        kind="tool_call",
                        reference=call.name,
                        summary=(
                            f"Confluence graph: {call.name}({call.input}) → "
                            f"{_summarize_mcp_result(mcp_result)}"
                        ),
                    )
                )
                tool_result_blocks.append(
                    {
                        "toolResult": {
                            "toolUseId": call.id,
                            "content": [{"json": mcp_result}],
                        }
                    }
                )
            except MCPToolError as exc:
                logger.info(
                    "confluence_context_tool_call_failed tool=%s error=%s", call.name, str(exc)
                )
                evidence.append(
                    Evidence(
                        kind="tool_call",
                        reference=call.name,
                        summary=f"Confluence graph call failed: {exc}",
                    )
                )
                tool_result_blocks.append(
                    {
                        "toolResult": {
                            "toolUseId": call.id,
                            "content": [{"text": f"Error: {exc}"}],
                            "status": "error",
                        }
                    }
                )
        messages.append({"role": "user", "content": tool_result_blocks})

    logger.info("confluence_context_turn_limit_reached issue_key=%s", jira_issue_key)
    return None, evidence
