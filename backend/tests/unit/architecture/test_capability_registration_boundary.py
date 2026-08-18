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
        # Phase 7 (minimal integration) — three files added deliberately,
        # for the same reason `control_plane.py` was added in Phase 3:
        #
        # `app/control_plane/runtime.py` is the first genuine STARTUP-TIME
        # caller `register_all_capabilities()` was always meant to have
        # (Phase 2's own docstring: "Nothing in the running application
        # consumes a CapabilityRegistry yet... Phase 3 imports and calls
        # when it actually needs the registry to exist at request time" —
        # this is that call, made once, at `app.main.create_app()` time,
        # never from a request handler; see `test_no_http_router_imports_
        # capability_registration` below, still passing, for the
        # complementary "no API router" guarantee this doesn't weaken).
        # It calls only the ALREADY-trusted `register_all_capabilities`
        # function — never `.register()` directly on the registry object
        # itself (confirmed: grep finds zero `.register(` calls in this
        # file outside its own docstring prose).
        #
        # `app/services/engineering_task_service.py` and
        # `app/api/v1/routers/engineering_tasks.py` hold a
        # `CapabilityRegistry` only as an ALREADY-POPULATED, injected
        # constructor/dependency parameter — read-only, passed straight
        # through to `ControlPlane` (which itself only ever calls `.get()`,
        # per `test_control_plane_never_calls_register_on_capability_registry`
        # below) — never registering anything themselves.
        "app/control_plane/runtime.py",
        "app/services/engineering_task_service.py",
        "app/api/v1/routers/engineering_tasks.py",
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


# Phase 7 gap closed: the import-boundary check above cannot distinguish
# read (.get()) from write (.register()) access, and — until this test —
# the ONLY file with a `.register()`-call AST check was
# `control_plane.py` (the one existing test right above this comment),
# hardcoded to that single path. The three Phase 7 additions to
# `_ALLOWED_IMPORTERS_OF_CAPABILITY_REGISTRY` were left with NO structural
# guarantee against a future `.register()` call being added inside them —
# a real, found gap, closed here rather than silently left open.
_PHASE_7_READ_ONLY_CAPABILITY_REGISTRY_CONSUMERS: tuple[str, ...] = (
    "control_plane/runtime.py",
    "services/engineering_task_service.py",
    "api/v1/routers/engineering_tasks.py",
)


def test_phase_7_capability_registry_consumers_never_call_register_directly() -> None:
    """`app/control_plane/runtime.py` legitimately calls the TRUSTED
    `register_all_capabilities(...)` function (a plain function call, not
    a `.register(...)` method call on the registry object) exactly once,
    at startup — that call is intentionally NOT what this test forbids.
    What it forbids, for all three files, is any direct
    `<expr>.register(...)` attribute-call on the `CapabilityRegistry`
    object itself, which would bypass `app.capabilities.setup` entirely."""
    for relative_path in _PHASE_7_READ_ONLY_CAPABILITY_REGISTRY_CONSUMERS:
        path = APP_ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offending_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
        ]
        assert not offending_lines, (
            f"app/{relative_path} calls .register(...) at line(s) {offending_lines} "
            "— this file is allowlisted as a READ-ONLY CapabilityRegistry consumer; "
            "registration authority remains exclusively "
            "app.capabilities.setup.register_all_capabilities (Cap §10)."
        )
