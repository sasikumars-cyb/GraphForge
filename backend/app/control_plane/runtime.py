"""Process-wide `CapabilityRegistry`/`PolicyStore` composition — Phase 7's
minimal integration.

**This module is NOT a second Control Plane and NOT a new authority
layer.** It does exactly five things:

1. Construct the process-wide `CapabilityRegistry`.
2. Register capabilities exactly once, via the existing, unmodified
   `app.capabilities.setup.register_all_capabilities`.
3. Construct the process-wide `PolicyStore`.
4. Seed the existing system Policy for `query_knowledge_graph` exactly
   once, via the existing, unmodified
   `app.control_plane.policy.seed_system_policy_allowing`.
5. Expose read-only accessors for the two instances it built.

It never authorizes an Action, never executes a Tool, never makes a
Policy *decision* (only loads Policy *data*, exactly as `PolicyStore.
load()` already exists to do), never issues a Grant, never touches
Workspace or Verification, and never appends an Engineering State event
of any kind. `ControlPlane` remains the sole authority over every one of
those — this module only hands `ControlPlane`'s constructor the two
already-existing dependency objects it has always required.

Mirrors `app.tools.registry`'s `_registry = ToolRegistry()` / `
get_tool_registry()` module-level-singleton shape exactly, and `app.main`
's `register_all_tools()` startup-call-site convention exactly — no new
mechanism invented.

**Explicit initialization, never implicit import-order reliance:**
`get_capability_registry()`/`get_policy_store()` raise
`ControlPlaneRuntimeNotInitializedError` if called before
`bootstrap_control_plane_runtime()` has run — there is no lazy
self-initializing fallback.

**Idempotent, matching `register_all_tools()`'s own established
precedent and for the identical reason:** `app.main.create_app()` is
called multiple times across the test suite (each `db_client`/`client`
fixture invocation), so this module's own startup call site is invoked
repeatedly in-process. Unlike `ToolRegistry.register()`, `CapabilityRegistry
.register()` and `PolicyStore.load()` are NOT safe to call twice with the
same content (the former raises `CapabilityAlreadyRegisteredError`; the
latter would silently re-seed identical data, harmless but wasteful) — so
this module makes its OWN one-time bootstrap idempotent explicitly,
rather than relying on either of those.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.capabilities.registry import CapabilityRegistry
from app.capabilities.setup import register_all_capabilities
from app.control_plane.policy import PolicyScopeLevel, PolicyStore, seed_system_policy_allowing
from app.tools.registry import get_tool_registry

_QUERY_KNOWLEDGE_GRAPH_CAPABILITY_ID = "query_knowledge_graph"

_capability_registry = CapabilityRegistry(tool_registry=get_tool_registry())
_policy_store = PolicyStore()
_bootstrapped = False


class ControlPlaneRuntimeNotInitializedError(RuntimeError):
    """Raised by the accessors below if called before
    `bootstrap_control_plane_runtime()` — never a silent, implicitly
    self-initializing fallback."""


def bootstrap_control_plane_runtime() -> None:
    """Call exactly once, at application startup — mirrors
    `register_all_tools()`'s own call site in `app.main.create_app()`
    (called immediately alongside it). Idempotent: a second call is a
    harmless no-op, not a re-registration attempt (see this module's own
    docstring for why that distinction matters here specifically).
    """
    global _bootstrapped
    if _bootstrapped:
        return

    register_all_capabilities(_capability_registry)
    _policy_store.load(
        PolicyScopeLevel.SYSTEM,
        seed_system_policy_allowing(
            _QUERY_KNOWLEDGE_GRAPH_CAPABILITY_ID,
            authored_by="app_startup",
            effective_at=datetime.now(UTC).isoformat(),
        ),
    )
    _bootstrapped = True


def get_capability_registry() -> CapabilityRegistry:
    """The process-wide `CapabilityRegistry`, already populated by
    `bootstrap_control_plane_runtime()`. Raises
    `ControlPlaneRuntimeNotInitializedError` if bootstrap hasn't run —
    never returns an empty, silently-unusable registry."""
    if not _bootstrapped:
        raise ControlPlaneRuntimeNotInitializedError(
            "bootstrap_control_plane_runtime() has not run yet — call it at "
            "application startup (see app.main.create_app) before any request "
            "reaches this accessor."
        )
    return _capability_registry


def get_policy_store() -> PolicyStore:
    """The process-wide `PolicyStore`, already seeded by
    `bootstrap_control_plane_runtime()`. Same fail-clearly discipline as
    `get_capability_registry()` above."""
    if not _bootstrapped:
        raise ControlPlaneRuntimeNotInitializedError(
            "bootstrap_control_plane_runtime() has not run yet — call it at "
            "application startup (see app.main.create_app) before any request "
            "reaches this accessor."
        )
    return _policy_store


__all__ = [
    "ControlPlaneRuntimeNotInitializedError",
    "bootstrap_control_plane_runtime",
    "get_capability_registry",
    "get_policy_store",
]
