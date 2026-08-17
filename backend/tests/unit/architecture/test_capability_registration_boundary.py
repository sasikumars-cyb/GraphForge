"""Phase 2, guardrail — Capability registration authority.

Capabilities contract §10/Phase 2 §11: "an agent/Reasoning Plane MUST NOT
be able to dynamically register a new privileged Capability merely by
generating model output. Registration must be controlled by trusted
application code/configuration." `app.capabilities.setup` is that trusted
code, mirroring `app.agents.setup`/`app.tools.setup`'s existing
convention exactly (docstring: "only this file calls
registry.register()"). This test makes that convention structurally
checked, not merely stated.
"""

from __future__ import annotations

import ast

from tests.unit.architecture._source_scan import APP_ROOT, find_imports_of, relative

# The only files permitted to import CapabilityRegistry at all. Tests are
# included deliberately — this repository's own tests construct
# CapabilityRegistry instances directly to verify its behavior, which is
# not the same thing as an application code path that could register a
# privileged Capability at runtime from model output.
#
# `app/control_plane/control_plane.py` was added in Phase 3, deliberately
# — `CapabilityRegistry.register()`'s own docstring names this exact seam:
# "the seam Phase 3's Control Plane will use is simply: look up a
# CapabilityVersion here... AFTER its own authorization pipeline runs."
# `ControlPlane` calls only `.get()` (a read), never `.register()` — this
# boundary test cannot distinguish read from write calls by import alone,
# so the guarantee that matters is enforced separately: no test in
# `tests/unit/control_plane/` ever calls `.register()` on an injected
# registry, and `ControlPlane` itself has no code path that does either.
_ALLOWED_IMPORTERS_OF_CAPABILITY_REGISTRY: frozenset[str] = frozenset(
    {
        "app/capabilities/registry.py",
        "app/capabilities/setup.py",
        "app/control_plane/control_plane.py",
    }
)
_TEST_PATH_PREFIXES: tuple[str, ...] = ("tests/",)


def test_capability_registry_is_not_imported_outside_the_trusted_boundary() -> None:
    hits = find_imports_of("app.capabilities.registry", symbol="CapabilityRegistry")

    offenders = {
        relative(path)
        for path in hits
        if relative(path) not in _ALLOWED_IMPORTERS_OF_CAPABILITY_REGISTRY
        and not relative(path).startswith(_TEST_PATH_PREFIXES)
    }

    assert not offenders, (
        f"CapabilityRegistry imported outside the trusted registration "
        f"boundary: {sorted(offenders)}. Only app.capabilities.setup may "
        "call register() — per Cap §10/Phase 2 §11, no agent, router, or "
        "Reasoning-Plane-adjacent module may register a Capability from "
        "model output or a request handler."
    )


def test_no_http_router_imports_capability_registration() -> None:
    """A stricter, independent check: no `app/api/` router — the one
    place a request (and therefore, indirectly, model-influenced input)
    could reach — imports the setup module at all."""
    hits = find_imports_of("app.capabilities.setup")
    offenders = {relative(path) for path in hits if relative(path).startswith("app/api/")}
    assert not offenders, (
        f"An API router imports app.capabilities.setup: {sorted(offenders)}. "
        "Capability registration must remain reachable only from trusted "
        "startup code, never from request handling."
    )


def test_control_plane_never_calls_register_on_capability_registry() -> None:
    """AST-based, not textual: `app/control_plane/control_plane.py` may
    hold a `CapabilityRegistry` (added to the allowlist above for lookup),
    but MUST NOT call `.register(...)` on it anywhere — that authority
    remains exclusively `app.capabilities.setup`'s per Cap §10. Checking
    the method name of every `Attribute` call node is a real structural
    guarantee, not merely "no import of register" (which the import-level
    check above cannot express, since `register` is a method, not a
    top-level import)."""
    path = APP_ROOT / "control_plane" / "control_plane.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offending_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register"
    ]
    assert not offending_lines, (
        f"app/control_plane/control_plane.py calls .register(...) at line(s) "
        f"{offending_lines} — ControlPlane must only ever call "
        "CapabilityRegistry.get()/all_versions() (lookup), never register() "
        "(Cap §10: registration is an out-of-band engineering act)."
    )
