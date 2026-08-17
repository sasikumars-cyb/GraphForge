"""Contract tests for `app.capabilities.registry.CapabilityRegistry`.

Uses a minimal, controlled `ToolRegistry` (real class, fake specs) rather
than the process-wide singleton — no database, no real Tool
implementations, fast and hermetic, matching this repository's existing
`test_manifest_dependency_integrity.py`-style convention for registry
tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.capabilities.model import (
    CapabilityVersion,
    IsolationRequirement,
    ReversibilityClass,
    RiskClass,
    SideEffectClass,
)
from app.capabilities.registry import (
    CapabilityAlreadyRegisteredError,
    CapabilityRegistry,
    ExternalWriteCapabilityFrozenError,
    RecursiveCompensationError,
    UnknownToolBindingError,
)
from app.tools.interfaces import ToolCategory, ToolHealth, ToolInput, ToolResult
from app.tools.registry import ToolRegistry, ToolSpec


class _FakeTool:
    tool_id = "fake_tool"
    display_name = "Fake"
    description = "d"
    category = ToolCategory.CUSTOM
    capabilities: list[str] = []

    def __init__(self, config: dict[str, Any]) -> None:
        pass

    async def execute(self, input: ToolInput) -> ToolResult:
        return ToolResult(tool_id=self.tool_id, tool_name=self.display_name, success=True)

    async def health_check(self) -> ToolHealth:
        return ToolHealth.HEALTHY

    def requires_auth(self) -> bool:
        return False


def _tool_registry_with(*tool_ids: str) -> ToolRegistry:
    registry = ToolRegistry()
    for tid in tool_ids:
        registry.register(
            ToolSpec(
                tool_id=tid,
                display_name=tid,
                description="d",
                category=ToolCategory.CUSTOM,
                capabilities=[],
                factory=lambda cfg: _FakeTool(cfg),
            )
        )
    return registry


def _capability(**overrides: object) -> CapabilityVersion:
    kwargs = dict(
        capability_id="cap_a",
        version=1,
        description="d",
        input_schema={},
        output_schema={},
        scope_ceiling="s",
        risk_class=RiskClass.LOW,
        reversibility=ReversibilityClass.REVERSIBLE,
        compensating_capability_id=None,
        external_visibility=False,
        side_effect_class=SideEffectClass.READ_ONLY,
        required_authorization="none",
        isolation_requirement=IsolationRequirement.NONE,
        execution_context_requirements=(),
        produces_artifact=False,
        tool_id="fake_tool",
        registered_by="test",
    )
    kwargs.update(overrides)
    return CapabilityVersion(**kwargs)


def test_register_and_get() -> None:
    registry = CapabilityRegistry(_tool_registry_with("fake_tool"))
    cap = _capability()
    registry.register(cap)

    assert registry.get("cap_a") == cap
    assert registry.get("cap_a", version=1) == cap
    assert registry.get("cap_a", version=2) is None
    assert registry.get("nonexistent") is None


def test_version_resolution_returns_the_latest_by_default() -> None:
    registry = CapabilityRegistry(_tool_registry_with("fake_tool"))
    v1 = _capability(version=1, description="first")
    v2 = _capability(version=2, description="second")
    registry.register(v1)
    registry.register(v2)

    assert registry.get("cap_a") == v2
    assert registry.get("cap_a", version=1) == v1
    assert registry.all_versions("cap_a") == [v1, v2]


def test_existing_version_cannot_be_silently_mutated() -> None:
    """Cap §3: a Capability version is immutable once registered. Not
    even byte-identical re-registration is permitted — the correct path
    is a new version number."""
    registry = CapabilityRegistry(_tool_registry_with("fake_tool"))
    registry.register(_capability())

    with pytest.raises(CapabilityAlreadyRegisteredError):
        registry.register(_capability())  # identical content, same version

    with pytest.raises(CapabilityAlreadyRegisteredError):
        registry.register(_capability(description="a changed description"))


def test_unknown_tool_binding_is_rejected() -> None:
    registry = CapabilityRegistry(_tool_registry_with("some_other_tool"))
    with pytest.raises(UnknownToolBindingError):
        registry.register(_capability(tool_id="fake_tool"))


def test_external_write_capability_registration_is_frozen() -> None:
    """The Phase 0/8 freeze, still active: no NEW external-write
    Capability may be registered before Phase 8."""
    registry = CapabilityRegistry(_tool_registry_with("fake_tool"))
    with pytest.raises(ExternalWriteCapabilityFrozenError):
        registry.register(_capability(side_effect_class=SideEffectClass.EXTERNAL_WRITE))


def test_recursive_compensation_is_rejected() -> None:
    """Cap §3.1: a compensating Capability must not itself be
    compensatable."""
    registry = CapabilityRegistry(_tool_registry_with("fake_tool"))

    # cap_b is registered as COMPENSATABLE (citing some terminal
    # compensator not itself under test).
    registry.register(
        _capability(
            capability_id="cap_b",
            reversibility=ReversibilityClass.COMPENSATABLE,
            compensating_capability_id="cap_c",
        )
    )

    # cap_a now tries to name cap_b — itself COMPENSATABLE — as ITS
    # compensator. Must be rejected.
    with pytest.raises(RecursiveCompensationError):
        registry.register(
            _capability(
                capability_id="cap_a",
                reversibility=ReversibilityClass.COMPENSATABLE,
                compensating_capability_id="cap_b",
            )
        )


def test_compensator_forward_reference_is_allowed() -> None:
    """A compensator not yet registered is not itself an error — only a
    REGISTERED, COMPENSATABLE compensator is rejected. See
    CapabilityRegistry.register()'s own comment on this scoping choice."""
    registry = CapabilityRegistry(_tool_registry_with("fake_tool"))
    registry.register(
        _capability(
            capability_id="cap_a",
            reversibility=ReversibilityClass.COMPENSATABLE,
            compensating_capability_id="not_yet_registered",
        )
    )
    assert registry.get("cap_a") is not None


def test_registry_has_no_execute_or_dispatch_method() -> None:
    """Structural proof, not just documentation, that the Control Plane
    boundary's absence is explicit: CapabilityRegistry cannot be asked to
    run anything."""
    forbidden_method_names = {"execute", "dispatch", "run", "invoke", "call"}
    actual_methods = {name for name in dir(CapabilityRegistry) if not name.startswith("_")}
    assert not (forbidden_method_names & actual_methods), (
        f"CapabilityRegistry unexpectedly exposes {forbidden_method_names & actual_methods} "
        "— it must remain lookup-only until the Control Plane (Phase 3) "
        "is the one deciding whether a Capability may execute."
    )
