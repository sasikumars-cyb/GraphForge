"""Phase 4 guardrail — the Control Plane is the sole Workspace lifecycle
authority (Cap §19: "The Control Plane owns Workspace lifecycle...
The Reasoning Plane MAY only propose; proposals route through the
Control Plane. The thing being isolated MUST NOT control its own
isolation.").

`WorkspaceLifecycleService` is an implementation component, not a second
authority — mirrors `test_control_plane_authorization_boundary.py`'s
exact pattern and rationale, applied to Workspace instead of
Authorization Grants.
"""

from __future__ import annotations

from tests.unit.architecture._source_scan import find_imports_of, relative

_INTERNAL_TO_CONTROL_PLANE_PREFIX = "app/control_plane/"
_TEST_PATH_PREFIXES: tuple[str, ...] = ("tests/",)

_ALLOWED_WORKSPACE_SERVICE_IMPORTERS: frozenset[str] = frozenset()


def test_workspace_lifecycle_service_is_not_imported_outside_the_control_plane() -> None:
    hits = find_imports_of(
        "app.control_plane.workspace_lifecycle", symbol="WorkspaceLifecycleService"
    )
    offenders = {
        relative(path)
        for path in hits
        if not relative(path).startswith(_INTERNAL_TO_CONTROL_PLANE_PREFIX)
        and not relative(path).startswith(_TEST_PATH_PREFIXES)
        and relative(path) not in _ALLOWED_WORKSPACE_SERVICE_IMPORTERS
    }
    assert not offenders, (
        f"WorkspaceLifecycleService imported outside app/control_plane/: "
        f"{sorted(offenders)}. Cap §19: the Control Plane is the sole authority "
        "over Workspace lifecycle — nothing Reasoning-Plane-adjacent may "
        "construct or call this service directly."
    )


def test_only_workspace_lifecycle_module_has_both_event_repository_and_workspace_vocabulary() -> (
    None
):
    """Same discipline as the Authorization boundary test: confirms the
    one file with both (a) `EngineeringEventRepository` access and (b)
    the `WORKSPACE_CREATED` event-type vocabulary is
    `workspace_lifecycle.py` — i.e. nothing else has both the means and
    the vocabulary to append a `Workspace*` event."""
    repo_importers = {
        relative(p) for p in find_imports_of("app.repositories.engineering_event_repository")
    }
    workspace_const_importers = {
        relative(p)
        for p in find_imports_of("app.engineering_state.events", symbol="WORKSPACE_CREATED")
    }
    both = repo_importers & workspace_const_importers
    offenders = {
        f
        for f in both
        if not f.startswith(_INTERNAL_TO_CONTROL_PLANE_PREFIX)
        and not f.startswith(_TEST_PATH_PREFIXES)
    }
    assert not offenders, (
        f"Unexpected set of modules with both Engineering Event append access "
        f"and Workspace* event vocabulary: {sorted(offenders)}. Only "
        "app/control_plane/workspace_lifecycle.py should have both."
    )
    assert "app/control_plane/workspace_lifecycle.py" in both, (
        "app/control_plane/workspace_lifecycle.py no longer imports both "
        "EngineeringEventRepository and the WORKSPACE_CREATED constant — "
        "this test's own premise is stale; update it alongside whatever "
        "structural change caused this."
    )


def test_physical_workspace_mutation_is_not_reachable_outside_the_control_plane() -> None:
    """The actual filesystem-mutating primitives — `create_physical_workspace`
    (runs `git clone`) and `destroy_physical_workspace` (removes a
    directory tree) — must not be reachable from outside
    `app/control_plane/`. This is the Workspace-specific analogue of the
    Phase 0 Tool-execution boundary: the thing being isolated (the
    physical Workspace) must not be triggerable except through the
    Control Plane's own lifecycle operations."""
    creators = find_imports_of(
        "app.control_plane.workspace_physical", symbol="create_physical_workspace"
    )
    destroyers = find_imports_of(
        "app.control_plane.workspace_physical", symbol="destroy_physical_workspace"
    )
    offenders = {
        relative(path)
        for path in {*creators, *destroyers}
        if not relative(path).startswith(_INTERNAL_TO_CONTROL_PLANE_PREFIX)
        and not relative(path).startswith(_TEST_PATH_PREFIXES)
    }
    assert not offenders, (
        f"Physical Workspace create/destroy primitives imported outside "
        f"app/control_plane/: {sorted(offenders)}. Cap §19: 'the thing being "
        "isolated MUST NOT control its own isolation' — only "
        "WorkspaceLifecycleService may trigger physical Workspace mutation."
    )
