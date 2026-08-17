"""Proves the Phase 2 representative Capability registers correctly
against the REAL Tool registry (not a fake one) — i.e. that
`neo4j_graph` genuinely exists as a Tool by the time
`register_all_capabilities` expects to bind to it.
"""

from __future__ import annotations

import pytest

from app.capabilities.model import CapabilityKind, ReversibilityClass, SideEffectClass
from app.capabilities.registry import CapabilityAlreadyRegisteredError, CapabilityRegistry
from app.capabilities.setup import register_all_capabilities
from app.tools.registry import get_tool_registry
from app.tools.setup import register_all_tools


def test_representative_capability_registers_against_real_tools() -> None:
    register_all_tools()  # populates the process-wide ToolRegistry singleton
    capability_registry = CapabilityRegistry(get_tool_registry())

    register_all_capabilities(capability_registry)

    capability = capability_registry.get("query_knowledge_graph")
    assert capability is not None
    assert capability.tool_id == "neo4j_graph"
    assert capability.side_effect_class == SideEffectClass.READ_ONLY
    assert capability.reversibility == ReversibilityClass.REVERSIBLE
    assert capability.external_visibility is False
    assert capability.produces_artifact is False
    assert capability.kind == CapabilityKind.PRIMITIVE
    assert capability.registered_by == "app.capabilities.setup.register_all_capabilities"


def test_registering_capabilities_twice_is_not_idempotent() -> None:
    """Confirms, at the setup-module level, that calling
    register_all_capabilities twice against the same registry instance
    fails loudly (immutability) rather than silently succeeding — the
    caller (a future startup sequence) must call this exactly once per
    process, unlike app.tools.setup.register_all_tools, whose own
    idempotency is a different, deliberate choice this module does not
    share."""
    register_all_tools()
    capability_registry = CapabilityRegistry(get_tool_registry())
    register_all_capabilities(capability_registry)

    with pytest.raises(CapabilityAlreadyRegisteredError):
        register_all_capabilities(capability_registry)
