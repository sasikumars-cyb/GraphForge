"""ToolRegistry — runtime catalog of all engineering tools.

Design mirrors app/ai/providers/registry.py:
  - ToolSpec is the declarative registration descriptor (like ProviderSpec)
  - ToolRegistry is the singleton catalog
  - Tools are registered at startup; the registry is read-only during requests

Agents call discover() to find tools that match their capability needs, then
invoke them through ToolExecutor. They never import implementation classes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.tools.interfaces import ITool, ToolCategory, ToolHealth

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """Declarative registration descriptor for one tool type.

    `factory` receives the tool's runtime config dict (API keys, base URLs,
    etc.) and returns a fully-constructed ITool. For tools that need no
    external config (e.g. Neo4j, which uses the app-level driver singleton),
    `factory` may ignore the config argument.

    `auth_fields` lists the config-dict keys that hold authentication secrets.
    The UI uses this to render the right input fields in the configuration panel.

    `default_enabled` controls whether the tool is active without an explicit
    DB configuration entry — internal tools (Neo4j, RepositoryIndex) default to
    True; external tools that need credentials default to False.
    """

    tool_id: str
    display_name: str
    description: str
    category: ToolCategory
    capabilities: list[str]
    factory: Callable[[dict[str, Any]], ITool]
    requires_auth: bool = False
    auth_fields: list[str] = field(default_factory=list)
    default_enabled: bool = True
    icon: str = "🔧"
    notes: str = ""


class ToolRegistry:
    """Central registry of engineering tools available to all agents.

    Lifecycle:
      1. Application startup: register_all_tools() calls register() for each spec.
      2. Startup or config change: configure() is called for each tool with DB-
         stored settings (enabled flag + decrypted auth config).
      3. During a request: agents call discover() to find relevant tools, then
         pass tool_ids to ToolExecutor. The registry provides get_tool().

    The registry is intentionally not thread-safe for writes (registration
    and configuration happen before any concurrent requests).
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._instances: dict[str, ITool] = {}
        self._health: dict[str, ToolHealth] = {}
        self._enabled: dict[str, bool] = {}

    # ── Registration (startup only) ────────────────────────────────────────

    def register(self, spec: ToolSpec) -> None:
        """Register a tool spec. Idempotent: re-registration replaces the spec."""
        self._specs[spec.tool_id] = spec
        # Respect default_enabled unless configure() has already been called
        if spec.tool_id not in self._enabled:
            self._enabled[spec.tool_id] = spec.default_enabled
        # Instantiate immediately for tools that are enabled by default and
        # require no external config (auth_fields is empty)
        if self._enabled[spec.tool_id] and not spec.requires_auth:
            try:
                self._instances[spec.tool_id] = spec.factory({})
                logger.info("tool_registered tool=%s auto_enabled=True", spec.tool_id)
            except Exception:
                logger.warning(
                    "tool_registration_failed tool=%s", spec.tool_id, exc_info=True
                )
        else:
            logger.info(
                "tool_registered tool=%s enabled=%s requires_auth=%s",
                spec.tool_id,
                self._enabled[spec.tool_id],
                spec.requires_auth,
            )

    # ── Configuration (from DB or environment) ─────────────────────────────

    def configure(self, tool_id: str, *, enabled: bool, config: dict[str, Any]) -> None:
        """Apply runtime configuration from the database.

        Called once at startup after the DB has been read, and again whenever
        the user changes a tool's settings via the API (same pattern as
        ConfigSnapshot.invalidate() → refresh() in the AI workspace).
        """
        if tool_id not in self._specs:
            logger.warning("tool_configure_unknown tool=%s", tool_id)
            return

        self._enabled[tool_id] = enabled

        if enabled:
            spec = self._specs[tool_id]
            try:
                self._instances[tool_id] = spec.factory(config)
                logger.info("tool_configured tool=%s enabled=True", tool_id)
            except Exception:
                logger.error(
                    "tool_configure_factory_failed tool=%s", tool_id, exc_info=True
                )
                self._instances.pop(tool_id, None)
        else:
            self._instances.pop(tool_id, None)
            logger.info("tool_configured tool=%s enabled=False", tool_id)

    # ── Discovery (agents call this) ───────────────────────────────────────

    def discover(self, capabilities: list[str] | None = None) -> list[ToolSpec]:
        """Return specs for all enabled tools that provide any of the requested capabilities.

        With no capabilities filter, returns all enabled tools. The Planning
        Agent uses this to build its tool execution plan.
        """
        results = []
        for tid, spec in self._specs.items():
            if not self._enabled.get(tid, False):
                continue
            if tid not in self._instances:
                continue  # enabled in config but factory failed — skip
            if capabilities is None or any(c in spec.capabilities for c in capabilities):
                results.append(spec)
        return results

    # ── Execution support ──────────────────────────────────────────────────

    def get_tool(self, tool_id: str) -> ITool | None:
        """Return the live tool instance, or None if not available."""
        return self._instances.get(tool_id)

    # ── Introspection (for UI and API) ─────────────────────────────────────

    def all_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def is_enabled(self, tool_id: str) -> bool:
        return self._enabled.get(tool_id, False) and tool_id in self._instances

    def get_health(self, tool_id: str) -> ToolHealth:
        if tool_id not in self._instances:
            return ToolHealth.UNCONFIGURED
        return self._health.get(tool_id, ToolHealth.UNCONFIGURED)

    async def check_health(self, tool_id: str) -> ToolHealth:
        """Run a live health check for one tool and cache the result."""
        tool = self._instances.get(tool_id)
        if tool is None:
            h = ToolHealth.UNCONFIGURED
        else:
            try:
                h = await tool.health_check()
            except Exception:
                logger.warning("tool_health_check_error tool=%s", tool_id, exc_info=True)
                h = ToolHealth.UNAVAILABLE
        self._health[tool_id] = h
        return h

    async def check_all_health(self) -> dict[str, ToolHealth]:
        """Concurrently check health for all registered tools."""
        tool_ids = list(self._specs.keys())
        results = await asyncio.gather(
            *(self.check_health(tid) for tid in tool_ids),
            return_exceptions=False,
        )
        return dict(zip(tool_ids, results))


# ---------------------------------------------------------------------------
# Module-level singleton — one registry for the lifetime of the process
# ---------------------------------------------------------------------------

_registry = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    """Return the application-wide ToolRegistry singleton."""
    return _registry
