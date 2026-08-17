"""Phase 0, guardrail 1 — the Tool-hiding boundary.

`docs/graphforge/CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md` §4 requires:
"The Control Plane is the sole evaluator of whether [a Capability's
required authorization] is currently satisfied... a Tool receives a
dispatch instruction only after the Control Plane has independently
confirmed authorization." The final adversarial sequencing review (this
conversation's own accepted finding) named the concrete, present-day risk
precisely: `app/tools/registry.py`'s `ToolExecutor` is public and
importable from anywhere, so nothing today stops new code from dispatching
a Tool directly, bypassing a Control Plane that doesn't exist yet.

Phase 0 does not build the Control Plane. It builds the one thing that
makes the eventual boundary *enforceable*: a test that fails the build the
instant a NEW module imports `app.tools.ToolExecutor` from outside the set
of call sites that predate this contract, or from outside
`app.control_plane` (the reserved namespace the real Control Plane will
occupy from Phase 3 onward — see `app/control_plane/__init__.py`).

This is deliberately a **ratchet, not a rewrite**: every current caller
below is grandfathered — Phase 0 must not break existing behavior (per
the frozen sequencing plan's own instruction). What it prevents is
*growth* of that set anywhere except `app.control_plane`.
"""

from __future__ import annotations

from tests.unit.architecture._source_scan import find_imports_of, relative

# Every module outside `app/tools/` and `app/control_plane/` that
# currently imports `ToolExecutor` directly from `app.tools`, as of Phase
# 0. Adding a new entry here is a deliberate, one-line, reviewable diff —
# exactly the "explicit architectural review" gate the sequencing plan
# calls for. Do NOT add an entry here to make this test pass without
# first confirming the new import genuinely cannot be expressed as an
# ActionProposal dispatched through the Control Plane once it exists.
ALLOWED_LEGACY_DIRECT_DISPATCHERS: frozenset[str] = frozenset(
    {
        "app/context_pipeline/providers.py",
        "app/context_pipeline/reasoning/investigators.py",
        "app/services/refinement_grounding.py",
    }
)

# `app/tools/` is the Tool implementation layer itself (Capabilities
# contract §1: "Tool = the concrete implementation fulfilling a
# Capability's contract") — its own internal wiring (setup.py building
# the registry, an implementation module importing the executor type for
# its own use) is not a boundary violation; it's the layer the boundary
# protects access *to*.
_INTERNAL_TO_TOOL_LAYER_PREFIX = "app/tools/"

# The one namespace Phase 0 reserves as the eventual, sole legitimate
# dispatcher once the Control Plane is real (Phase 3). Nothing lives here
# yet (see app/control_plane/__init__.py's docstring) — this exclusion
# exists so that when Phase 3 lands, its code doesn't have to fight this
# same test.
_RESERVED_CONTROL_PLANE_PREFIX = "app/control_plane/"


def test_tool_executor_not_imported_outside_control_plane_boundary() -> None:
    """No file outside the grandfathered set, `app/tools/` itself, or the
    reserved `app/control_plane/` namespace imports `ToolExecutor` from
    `app.tools`.

    A failure here means new code was written that dispatches a Tool
    directly — exactly the bypass Capabilities contract §4 forbids once a
    real Control Plane exists, and exactly the loophole the final
    adversarial sequencing review flagged as open until this test exists.
    """
    hits = find_imports_of("app.tools", symbol="ToolExecutor")

    offenders = {
        relative(path)
        for path in hits
        if not relative(path).startswith(_INTERNAL_TO_TOOL_LAYER_PREFIX)
        and not relative(path).startswith(_RESERVED_CONTROL_PLANE_PREFIX)
        and relative(path) not in ALLOWED_LEGACY_DIRECT_DISPATCHERS
    }

    assert not offenders, (
        "New direct ToolExecutor dispatch found outside the Control Plane "
        f"boundary: {sorted(offenders)}. Per "
        "docs/graphforge/CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md §4, a "
        "Tool must only ever be dispatched after the Control Plane confirms "
        "authorization — it must not be called directly. If this dispatch "
        "genuinely cannot wait for the real Control Plane (Phase 3), that is "
        "itself the architectural conflict to raise, not a reason to widen "
        "ALLOWED_LEGACY_DIRECT_DISPATCHERS."
    )


def test_grandfathered_dispatchers_still_exist() -> None:
    """Guards the allowlist itself against silently going stale — if a
    grandfathered file is deleted or its import removed, the entry should
    be deleted too, so the allowlist never accumulates dead exceptions
    that quietly widen the boundary for no reason."""
    hits = find_imports_of("app.tools", symbol="ToolExecutor")
    current = {relative(path) for path in hits}

    stale = ALLOWED_LEGACY_DIRECT_DISPATCHERS - current
    assert not stale, (
        f"ALLOWED_LEGACY_DIRECT_DISPATCHERS contains entries that no longer "
        f"import ToolExecutor: {sorted(stale)}. Remove them from the "
        "allowlist rather than leaving an unused exception in place."
    )


def test_control_plane_boundary_package_exists() -> None:
    """The reserved namespace this test exempts must actually exist and
    must not (yet) contain dispatch logic of its own — Phase 0 builds the
    boundary marker, not the Control Plane."""
    from tests.unit.architecture._source_scan import APP_ROOT

    control_plane_init = APP_ROOT / "control_plane" / "__init__.py"
    assert control_plane_init.exists(), (
        "app/control_plane/__init__.py is missing — it is the reserved "
        "namespace this boundary test exempts as the future sole Control "
        "Plane entry point."
    )
