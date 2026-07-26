"""ToolExecutor — isolated, timeout-bounded tool execution.

Agents never call tool.execute() directly. Everything goes through here:
  - timeout enforcement (no tool hangs the agent)
  - exception isolation (one tool failing doesn't abort the batch)
  - latency measurement
  - structured error results

Usage:
    executor = ToolExecutor(registry=get_tool_registry())
    results = await executor.execute_all([
        ("neo4j_graph", ToolInput(query=task, parameters={"db": db})),
    ])
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.tools.interfaces import ITool, ToolInput, ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 20.0
_DEFAULT_CONCURRENCY = 4


class ToolExecutor:
    """Executes tool calls on behalf of an agent.

    One executor per agent run — carries the per-request db session and
    any extras the tools may need via ToolInput.parameters.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        timeout_secs: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._registry = registry
        self._timeout = timeout_secs

    async def execute(self, tool_id: str, input: ToolInput) -> ToolResult:
        """Execute a single tool from the registry. Never raises — errors
        become ToolResult(success=False)."""
        tool = self._registry.get_tool(tool_id)
        spec = next((s for s in self._registry.all_specs() if s.tool_id == tool_id), None)
        display_name = spec.display_name if spec else tool_id

        if tool is None:
            return ToolResult(
                tool_id=tool_id,
                tool_name=display_name,
                success=False,
                error=f"Tool '{tool_id}' is not enabled or not configured.",
            )

        return await self.execute_instance(tool, tool_id, display_name, input)

    async def execute_instance(
        self, tool: ITool, tool_id: str, display_name: str, input: ToolInput
    ) -> ToolResult:
        """Execute a tool instance constructed outside the registry — for
        tools whose credentials are per-user rather than an install-wide
        config the singleton ToolRegistry can hold (e.g. GitHub's OAuth
        connection; see app.tools.implementations.github_tool). Same
        timeout/error-isolation guarantees as `execute()`, just without the
        registry lookup."""
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                tool.execute(input),
                timeout=self._timeout,
            )
            result.latency_ms = (time.monotonic() - start) * 1000
            logger.info(
                "tool_executor_success tool=%s latency_ms=%.0f cache_hit=%s",
                tool_id,
                result.latency_ms,
                result.cache_hit,
            )
            return result

        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning(
                "tool_executor_timeout tool=%s timeout_secs=%.1f", tool_id, self._timeout
            )
            return ToolResult(
                tool_id=tool_id,
                tool_name=display_name,
                success=False,
                latency_ms=elapsed,
                error=f"Tool timed out after {self._timeout:.0f}s.",
            )

        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.error(
                "tool_executor_failed tool=%s error=%s", tool_id, exc, exc_info=True
            )
            return ToolResult(
                tool_id=tool_id,
                tool_name=display_name,
                success=False,
                latency_ms=elapsed,
                error=str(exc),
            )

    async def execute_all(
        self,
        requests: list[tuple[str, ToolInput]],
        concurrency: int = _DEFAULT_CONCURRENCY,
    ) -> list[ToolResult]:
        """Execute a batch of tool calls concurrently, bounded by concurrency.

        Order of results matches order of `requests`. Failed tools produce
        ToolResult(success=False) entries rather than aborting the batch.
        """
        if not requests:
            return []

        semaphore = asyncio.Semaphore(concurrency)

        async def bounded(tool_id: str, inp: ToolInput) -> ToolResult:
            async with semaphore:
                return await self.execute(tool_id, inp)

        results = await asyncio.gather(
            *(bounded(tid, inp) for tid, inp in requests),
            return_exceptions=False,
        )
        return list(results)
