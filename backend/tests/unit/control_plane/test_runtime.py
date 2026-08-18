"""Contract tests for `app.control_plane.runtime` — Phase 7's minimal
`CapabilityRegistry`/`PolicyStore` process-wide composition.

No database needed — this module performs no I/O beyond in-memory
registration. `bootstrap_control_plane_runtime()` is idempotent (see its
own docstring for why), so tests 1-5/7 below call it directly against the
real module-level singleton without needing to isolate process state; only
test 6 (accessor-before-bootstrap) genuinely needs a fresh interpreter,
since by the time ANY test in this suite runs, some earlier fixture has
almost certainly already imported `app.main` and triggered bootstrap.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.capabilities.model import SideEffectClass
from app.control_plane.policy import PolicyScopeLevel
from app.control_plane.runtime import (
    bootstrap_control_plane_runtime,
    get_capability_registry,
    get_policy_store,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def test_bootstrap_creates_the_capability_registry_and_registers_capabilities() -> None:
    bootstrap_control_plane_runtime()
    registry = get_capability_registry()
    capability = registry.get("query_knowledge_graph", 1)
    assert capability is not None
    assert capability.tool_id == "neo4j_graph"
    assert capability.side_effect_class == SideEffectClass.READ_ONLY


def test_bootstrap_seeds_the_system_policy_for_query_knowledge_graph() -> None:
    bootstrap_control_plane_runtime()
    store = get_policy_store()
    decision = store.evaluate("query_knowledge_graph")
    assert decision.allowed is True

    # A capability that was never seeded remains fail-closed denied —
    # proves the bootstrap seeded exactly the one intended ALLOW, not a
    # blanket allow-everything policy.
    denied = store.evaluate("some_capability_never_registered")
    assert denied.allowed is False


def test_accessors_return_the_same_initialized_instances_on_repeated_calls() -> None:
    bootstrap_control_plane_runtime()
    registry_a = get_capability_registry()
    registry_b = get_capability_registry()
    store_a = get_policy_store()
    store_b = get_policy_store()

    assert registry_a is registry_b
    assert store_a is store_b


def test_repeated_bootstrap_is_an_idempotent_no_op() -> None:
    bootstrap_control_plane_runtime()
    registry_before = get_capability_registry()
    store_before = get_policy_store()

    # A second (and third) call must not raise
    # CapabilityAlreadyRegisteredError or re-seed a duplicate Policy rule.
    bootstrap_control_plane_runtime()
    bootstrap_control_plane_runtime()

    assert get_capability_registry() is registry_before
    assert get_policy_store() is store_before
    # Still exactly one loaded PolicyVersion at SYSTEM scope, unchanged by
    # the repeated calls above — a re-seed would have produced a NEW
    # version_id (PolicyVersion.version_id is content-derived).
    version_id_before = store_before.active_version_id(PolicyScopeLevel.SYSTEM)
    bootstrap_control_plane_runtime()
    assert store_before.active_version_id(PolicyScopeLevel.SYSTEM) == version_id_before


def test_runtime_module_imports_standalone_without_app_main() -> None:
    """No circular import: `app.control_plane.runtime` must be importable
    on its own, without ever needing `app.main` (which imports the API
    routers, which import this module) to already be loaded — proven in
    a fresh subprocess so no other test's prior imports can mask a real
    circular-import failure."""
    result = subprocess.run(
        [sys.executable, "-c", "import app.control_plane.runtime"],
        cwd=_BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_accessor_before_bootstrap_fails_clearly() -> None:
    """A genuinely fresh interpreter (never having called
    `bootstrap_control_plane_runtime()`) must raise
    `ControlPlaneRuntimeNotInitializedError` from the accessors — never
    silently construct/return an empty, unusable registry or store.
    Run in a subprocess: within this test *process*, some earlier
    fixture/test has almost certainly already imported `app.main` (which
    bootstraps at `create_app()` time), making this property untestable
    in-process without a fragile `importlib.reload`.
    """
    script = (
        "from app.control_plane.runtime import get_capability_registry, "
        "ControlPlaneRuntimeNotInitializedError\n"
        "try:\n"
        "    get_capability_registry()\n"
        "    raise SystemExit('FAIL: accessor did not raise before bootstrap')\n"
        "except ControlPlaneRuntimeNotInitializedError:\n"
        "    pass\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
