"""Tool Platform — foundational capability for AI agents in GraphForge.

Every engineering system is exposed as a reusable tool. Agents discover
and invoke tools through the registry; they never import tool implementation
classes directly. Adding a new system (Jira, Slack, Azure DevOps) requires
one Tool definition and one implementation — no agent changes, no LLM changes.

Public API:
    get_tool_registry()   → the singleton ToolRegistry
    ToolSpec              → declarative registration descriptor
    ITool                 → tool implementation contract
    ToolInput             → typed input for every tool call
    ToolResult            → typed output from every tool call
    ToolHealth            → health status enum
    ToolCategory          → category enum
    ToolExecutor          → isolated execution with timeout + retry
    ContextBuilder        → merges tool results into LLM-ready context
"""

from app.tools.interfaces import (
    ITool,
    ToolCategory,
    ToolHealth,
    ToolInput,
    ToolResult,
)
from app.tools.registry import ToolSpec, get_tool_registry
from app.tools.executor import ToolExecutor
from app.tools.context_builder import ContextBuilder, PlanningContext

__all__ = [
    "ITool",
    "ToolCategory",
    "ToolHealth",
    "ToolInput",
    "ToolResult",
    "ToolSpec",
    "get_tool_registry",
    "ToolExecutor",
    "ContextBuilder",
    "PlanningContext",
]
