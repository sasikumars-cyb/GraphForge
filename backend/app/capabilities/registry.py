"""`CapabilityRegistry` — Phase 2's registration/lookup layer.

Mirrors `app.tools.registry.ToolRegistry` and `app.orchestrator.registry.
AgentRegistry`'s own established convention: a plain in-process catalog,
populated once at startup by trusted application code (never by request
handling, never by agent/model output — see §11's registration-authority
requirement, enforced by `app.capabilities.setup` being the sole caller
of `register()`, proven by
`tests/unit/architecture/test_capability_registration_boundary.py`).

**This class has no `execute`/`dispatch`/`run` method, and that absence
is deliberate, not an oversight.** Capabilities contract §5: "The Control
Plane is the sole evaluator of whether [required authorization] is
currently satisfied." Phase 2 does not implement the Control Plane (see
`app.control_plane`'s own Phase 0 docstring — still empty). Adding a
dispatch method here would force one of two bad outcomes: either it
actually invokes the bound Tool with no authorization check at all
(exactly the bypass Cap inv. 1/2 forbid), or it fakes an
`authorized=True` the Phase 2 instructions explicitly forbid simulating.
Neither is acceptable, so neither exists. The seam Phase 3's Control
Plane will use is simply: look up a `CapabilityVersion` here, read its
`tool_id`, and call `app.tools.executor.ToolExecutor.execute(tool_id,
...)` itself, AFTER its own authorization pipeline runs — this registry
supplies the lookup, nothing more.
"""

from __future__ import annotations

import logging

from app.capabilities.model import CapabilityVersion, ReversibilityClass, SideEffectClass
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class CapabilityAlreadyRegisteredError(ValueError):
    """Raised when `register()` is called for a `(capability_id, version)`
    pair that already exists — Cap §3: a Capability version is immutable
    once registered. Unlike `ToolRegistry.register()` (deliberately
    idempotent — re-registration replaces the spec), this is NOT
    idempotent even for byte-identical content: the correct way to change
    a Capability's declaration is to register a new version number, never
    to re-register the same one — the same append-only discipline Phase 1
    already established for events, applied here to Capability
    declarations."""


class UnknownToolBindingError(ValueError):
    """Raised when a `CapabilityVersion.tool_id` does not name a
    currently-registered `ToolSpec` — Cap §1: "Capability ≠ Tool... a
    Capability's identity MUST survive its Tool being replaced," which
    presupposes the Tool it's bound to actually exists."""


class ExternalWriteCapabilityFrozenError(ValueError):
    """Raised when `register()` is given a Capability whose
    `side_effect_class` is `EXTERNAL_WRITE` — the Phase 0/8 freeze,
    unchanged: "no NEW external-write Capability may be introduced before
    Phase 8." `_authorization.py`'s existing `WRITE_GOALS` gate remains
    the only real external-write control until then."""


class RecursiveCompensationError(ValueError):
    """Raised when a `COMPENSATABLE` Capability's declared compensator is
    ITSELF `COMPENSATABLE` — Cap §3.1: "A compensating Capability MUST
    NOT itself be compensatable," preventing unbounded compensation
    chains."""


class CapabilityRegistry:
    """Central registry of Capability declarations.

    `tool_registry` is injected (not the global singleton reached for
    directly) so this class's own tests can construct a registry with a
    controlled, minimal set of Tools rather than depending on every real
    Tool `app.tools.setup.register_all_tools()` would otherwise wire up.
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._tool_registry = tool_registry
        self._versions: dict[tuple[str, int], CapabilityVersion] = {}
        self._latest_version: dict[str, int] = {}

    def register(self, capability: CapabilityVersion) -> None:
        """Register one immutable Capability version.

        Order of checks is deliberate: cheap/structural first (external-
        write freeze, Tool binding), immutability and cross-Capability
        checks last (they're the ones that need this registry's existing
        state, not just the candidate's own shape — `CapabilityVersion.
        __post_init__` already validated the candidate is internally
        well-formed before it ever reaches here).
        """
        if capability.side_effect_class == SideEffectClass.EXTERNAL_WRITE:
            raise ExternalWriteCapabilityFrozenError(
                f"{capability.capability_id} declares side_effect_class="
                "EXTERNAL_WRITE. No new external-write Capability may be "
                "registered before Phase 8 — see the frozen implementation "
                "sequencing plan and app.agents.git_ops._authorization's "
                "existing, unmigrated gate."
            )

        if not any(spec.tool_id == capability.tool_id for spec in self._tool_registry.all_specs()):
            raise UnknownToolBindingError(
                f"{capability.capability_id} v{capability.version} binds to "
                f"tool_id={capability.tool_id!r}, which is not registered in "
                "the given ToolRegistry."
            )

        key = (capability.capability_id, capability.version)
        if key in self._versions:
            raise CapabilityAlreadyRegisteredError(
                f"{capability.capability_id} v{capability.version} is "
                "already registered. Register a new version instead of "
                "re-registering an existing one — even with identical "
                "content."
            )

        if capability.reversibility == ReversibilityClass.COMPENSATABLE:
            # A forward reference (the compensator not registered yet) is
            # allowed at this phase — Phase 2 registers exactly one
            # capability, never a compensatable one, so this branch has
            # no real caller yet; the check that matters, and IS enforced
            # unconditionally, is: if the compensator IS already known
            # here, it must not itself be compensatable.
            compensator = self.get(capability.compensating_capability_id)  # type: ignore[arg-type]
            if compensator is not None and compensator.reversibility == (
                ReversibilityClass.COMPENSATABLE
            ):
                raise RecursiveCompensationError(
                    f"{capability.capability_id} v{capability.version} "
                    f"declares compensator {capability.compensating_capability_id!r}, "
                    "which is itself COMPENSATABLE (Cap §3.1 forbids this — "
                    "compensation chains are depth-1 by construction)."
                )

        self._versions[key] = capability
        self._latest_version[capability.capability_id] = max(
            self._latest_version.get(capability.capability_id, 0), capability.version
        )
        logger.info(
            "capability_registered capability_id=%s version=%d tool_id=%s registered_by=%s",
            capability.capability_id,
            capability.version,
            capability.tool_id,
            capability.registered_by,
        )

    def get(self, capability_id: str, version: int | None = None) -> CapabilityVersion | None:
        """The Capability at `version`, or the latest registered version
        if `version` is omitted. `None` if unknown."""
        resolved_version = (
            version if version is not None else self._latest_version.get(capability_id)
        )
        if resolved_version is None:
            return None
        return self._versions.get((capability_id, resolved_version))

    def all_versions(self, capability_id: str) -> list[CapabilityVersion]:
        """Every registered version of `capability_id`, ascending."""
        return sorted(
            (cap for (cid, _v), cap in self._versions.items() if cid == capability_id),
            key=lambda c: c.version,
        )

    def all_capabilities(self) -> list[CapabilityVersion]:
        """Every registered Capability version, across all capability_ids
        — introspection only (mirrors ToolRegistry.all_specs()), never
        consumed by anything performing dispatch."""
        return list(self._versions.values())
