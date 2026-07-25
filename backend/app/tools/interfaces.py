"""Core contracts for the GraphForge Tool Platform.

Every engineering tool must satisfy the ITool protocol. Agents interact
only through these types — implementation classes are never imported by agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class ToolCategory(str, Enum):
    GRAPH = "graph"
    CODE_INTELLIGENCE = "code_intelligence"
    PROJECT_MANAGEMENT = "project_management"
    DOCUMENTATION = "documentation"
    COMMUNICATION = "communication"
    MONITORING = "monitoring"
    FILESYSTEM = "filesystem"
    CUSTOM = "custom"


class ToolHealth(str, Enum):
    HEALTHY = "healthy"
    UNCONFIGURED = "unconfigured"
    OFFLINE = "offline"
    AUTH_FAILED = "auth_failed"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"


@dataclass
class ToolInput:
    """Typed input for a single tool execution.

    `query` is the natural-language planning question or engineering brief.
    `parameters` carries tool-specific runtime values (db session, relevance
    terms, filters) that would be awkward to encode in the query string.
    `max_results` caps how much data a tool may return to bound token usage.
    """

    query: str
    parameters: dict[str, Any] = field(default_factory=dict)
    max_results: int = 50


@dataclass
class ToolResult:
    """Structured output from a single tool execution.

    `data` is tool-specific and always contains a "context_text" key —
    a pre-formatted, LLM-ready string the ContextBuilder will stitch together
    with results from other tools.

    `evidence_items` is a list of human-readable strings suitable for the
    Evidence audit trail shown in the UI.

    `token_estimate` is a rough token count so the ContextBuilder can budget
    context space across many tool results.
    """

    tool_id: str
    tool_name: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    evidence_items: list[str] = field(default_factory=list)
    token_estimate: int = 0
    latency_ms: float = 0.0
    cache_hit: bool = False
    error: str | None = None


@runtime_checkable
class ITool(Protocol):
    """Contract every engineering tool must satisfy.

    Implementations live in app/tools/implementations/. Agents never
    import these classes — they receive ITool instances via the registry.
    """

    tool_id: str
    display_name: str
    description: str
    category: ToolCategory
    capabilities: list[str]

    async def execute(self, input: ToolInput) -> ToolResult:
        """Execute the tool and return a ToolResult.

        Must never raise — catches its own exceptions and returns a
        ToolResult with success=False and a descriptive error string.
        """
        ...

    async def health_check(self) -> ToolHealth:
        """Report tool health.

        Must never raise — catches its own exceptions and returns
        ToolHealth.UNAVAILABLE on unexpected errors.
        """
        ...

    def requires_auth(self) -> bool:
        """True if the tool needs externally-supplied authentication credentials."""
        ...
