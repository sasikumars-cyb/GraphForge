"""Capability registration — the sole module permitted to call
`CapabilityRegistry.register()`.

Mirrors `app.agents.setup`/`app.tools.setup` exactly: "only this file
calls registry.register(). [Capabilities] never self-register at module
import time." Phase 2 §11's registration-authority requirement — "an
agent/Reasoning Plane MUST NOT be able to dynamically register a new
privileged Capability merely by generating model output" — is enforced
by this being the ONE place that does, with no HTTP route, no agent
module, and no model-output path reaching `register()` anywhere else;
proven by `tests/unit/architecture/test_capability_registration_boundary.py`.

**Not wired into `app.main`'s startup, deliberately.** Nothing in the
running application consumes a `CapabilityRegistry` yet — no Control
Plane exists to look one up (Phase 3). Wiring this into live startup now
would be inert, unverifiable-by-use code; it is instead a standalone,
directly-tested module that Phase 3 imports and calls when it actually
needs the registry to exist at request time. This is a deliberate scope
choice, not an omission — see the Phase 2 final report for the same
point stated for a wider audience.
"""

from __future__ import annotations

from app.capabilities.model import (
    CapabilityKind,
    CapabilityVersion,
    IsolationRequirement,
    ReversibilityClass,
    RiskClass,
    SideEffectClass,
)
from app.capabilities.registry import CapabilityRegistry

_REGISTERED_BY = "app.capabilities.setup.register_all_capabilities"


def register_all_capabilities(registry: CapabilityRegistry) -> None:
    """Register the Phase 2 Capability set — exactly one, deliberately.

    `registry` must already be constructed against whichever
    `ToolRegistry` its Capabilities are meant to bind against (the
    process-wide singleton in real use, matching `app.tools.setup`'s own
    convention; a minimal, controlled one in tests)."""
    _register_query_knowledge_graph(registry)


def _register_query_knowledge_graph(registry: CapabilityRegistry) -> None:
    """The Phase 2 representative Capability — deliberately chosen per
    the instructions' own criteria: deterministic (a graph query),
    low-risk, read-only, no external write, no dependency on Policy
    complexity that doesn't exist yet. Binds to `neo4j_graph`
    (`app.tools.implementations.neo4j_tool.Neo4jGraphTool`), a real,
    already-working Tool requiring no external credentials.

    `execution_context_requirements` is empty: this Capability queries
    the current state of the indexed Knowledge Graph, not a pinned source
    revision — it has no Engineering State Execution Context (§7)
    dependency in the sense that concept exists for artifact-producing
    Capabilities. `produces_artifact=False` for the same reason: a query
    result is not a consumable artifact with an identity later Capability
    invocations bind to (Cap §14) — nothing here needed that mechanism,
    so nothing beyond the boolean was built for it.
    """
    registry.register(
        CapabilityVersion(
            capability_id="query_knowledge_graph",
            version=1,
            description=(
                "Read-only query of the indexed Neo4j Knowledge Graph for "
                "repositories, software components, and Kafka topics."
            ),
            input_schema={"query": "str", "parameters": "dict"},
            output_schema={"data": "dict", "summary": "str", "evidence_items": "list[str]"},
            scope_ceiling=(
                "the single Neo4j Knowledge Graph instance this deployment reads; read-only"
            ),
            risk_class=RiskClass.LOW,
            reversibility=ReversibilityClass.REVERSIBLE,
            compensating_capability_id=None,
            external_visibility=False,
            side_effect_class=SideEffectClass.READ_ONLY,
            required_authorization="none",
            isolation_requirement=IsolationRequirement.NONE,
            execution_context_requirements=(),
            produces_artifact=False,
            tool_id="neo4j_graph",
            registered_by=_REGISTERED_BY,
            kind=CapabilityKind.PRIMITIVE,
            composed_of=None,
            # Phase 9: `Neo4jGraphTool.execute` requires `db` (an
            # `AsyncSession`, to scope repository rows) and `user_id` (to
            # scope which user's repositories those rows are) — neither
            # is something the Reasoning Plane proposes; both are
            # Control-Plane-owned runtime dispatch dependencies. See
            # `ControlPlane._resolve_runtime_parameter`'s own docstring.
            runtime_injected_parameters=frozenset({"db", "user_id"}),
        )
    )


__all__ = ["register_all_capabilities"]
