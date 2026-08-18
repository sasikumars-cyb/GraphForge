"""Phase 7 guardrail — the Reasoning Plane may only propose; the Control
Plane remains the sole authority over execution, authorization, Workspace,
and Verification (Reasoning Engine contract §2/§5).

Mirrors `test_control_plane_authorization_boundary.py`/
`test_workspace_authority_boundary.py`/`test_verification_boundary.py`'s
exact pattern and rationale, applied to `ReasoningPlane` instead.
"""

from __future__ import annotations

import inspect

from app.reasoning_plane.plane import ReasoningPlane
from tests.unit.architecture._source_scan import find_imports_of, relative

_TEST_PATH_PREFIXES: tuple[str, ...] = ("tests/",)
_ENGINEERING_TASKS_ROUTER_PATH = "app/api/v1/routers/engineering_tasks.py"


def test_reasoning_plane_never_imports_tool_executor() -> None:
    """Never executes a Tool directly — Tool dispatch remains
    `ControlPlane`'s (via `ToolExecutor`) alone."""
    hits = find_imports_of("app.tools.executor", symbol="ToolExecutor")
    offenders = {relative(p) for p in hits if "reasoning_plane" in relative(p)}
    assert not offenders, (
        f"app/reasoning_plane imports ToolExecutor: {sorted(offenders)}. The Reasoning "
        "Plane must never execute a Tool directly."
    )


def test_reasoning_plane_never_imports_grant_issuance() -> None:
    """Never issues a Grant — Authorization remains `ControlPlane`'s
    alone."""
    hits = find_imports_of("app.control_plane.grant", symbol="AuthorizationGrant")
    offenders = {relative(p) for p in hits if "reasoning_plane" in relative(p)}
    assert not offenders, (
        f"app/reasoning_plane imports AuthorizationGrant: {sorted(offenders)}. The "
        "Reasoning Plane must never issue a Grant."
    )


def test_reasoning_plane_never_imports_workspace_lifecycle() -> None:
    """Never creates/touches a Workspace directly."""
    hits = find_imports_of(
        "app.control_plane.workspace_lifecycle", symbol="WorkspaceLifecycleService"
    )
    offenders = {relative(p) for p in hits if "reasoning_plane" in relative(p)}
    assert not offenders, (
        f"app/reasoning_plane imports WorkspaceLifecycleService: {sorted(offenders)}. "
        "The Reasoning Plane must never touch Workspace directly."
    )


def test_reasoning_plane_never_imports_verification_service() -> None:
    """Never requests or performs Verification directly — only
    `ControlPlane.request_verification` may."""
    hits = find_imports_of("app.control_plane.verification", symbol="VerificationService")
    offenders = {relative(p) for p in hits if "reasoning_plane" in relative(p)}
    assert not offenders, (
        f"app/reasoning_plane imports VerificationService: {sorted(offenders)}. The "
        "Reasoning Plane must never request or perform Verification directly."
    )


def test_reasoning_plane_never_appends_authorization_or_workspace_or_observation_events() -> None:
    """The only Engineering State event-type constants
    `app.reasoning_plane.plane` may import are `PLAN_CREATED`/
    `PLAN_STEP_CREATED` — never `AUTHORIZATION_*`/`WORKSPACE_*`/
    `OBSERVATION_RECORDED`, which reach Engineering State only through
    `ControlPlane`."""
    forbidden_symbols = (
        "AUTHORIZATION_GRANTED",
        "AUTHORIZATION_CONSUMING",
        "AUTHORIZATION_CONSUMED",
        "AUTHORIZATION_DENIED",
        "AUTHORIZATION_INVALIDATED",
        "WORKSPACE_CREATED",
        "OBSERVATION_RECORDED",
    )
    offenders: set[str] = set()
    for symbol in forbidden_symbols:
        hits = find_imports_of("app.engineering_state.events", symbol=symbol)
        offenders |= {relative(p) for p in hits if "reasoning_plane" in relative(p)}
    assert not offenders, (
        f"app/reasoning_plane imports Authorization*/Workspace*/Observation* event "
        f"vocabulary: {sorted(offenders)}. Only ControlPlane may append those event types."
    )


def test_control_plane_does_not_import_reasoning_plane() -> None:
    """One-directional dependency only: Reasoning -> Control, never the
    reverse. Control Plane contains no reasoning logic."""
    hits = find_imports_of("app.reasoning_plane.plane", symbol="ReasoningPlane")
    offenders = {
        relative(p)
        for p in hits
        if relative(p).startswith("app/control_plane/")
        and not relative(p).startswith(_TEST_PATH_PREFIXES)
    }
    assert not offenders, (
        f"app/control_plane imports ReasoningPlane: {sorted(offenders)}. The Control "
        "Plane must never depend on the Reasoning Plane."
    )


def test_reasoning_plane_run_signature_has_no_authority_bypassing_parameter() -> None:
    """`ReasoningPlane.run` must not accept anything that would let a
    caller supply a verifier identity, a Grant, or a Workspace reference
    — it only ever accepts `task_id` and opaque `capability_parameters`."""
    signature = inspect.signature(ReasoningPlane.run)
    forbidden_params = {
        "verifier_actor",
        "verifier_role",
        "grant",
        "grant_id",
        "workspace_id",
        "human_approval",
    }
    offending = forbidden_params & set(signature.parameters)
    assert not offending, (
        f"ReasoningPlane.run accepts authority-bypassing parameter(s) "
        f"{sorted(offending)} — it must construct only a transient ActionProposal, "
        "never anything resembling authorization/Grant/Workspace/Verification input."
    )


def test_only_engineering_tasks_boundary_appends_goal_created_outside_control_plane() -> None:
    """The one narrow approved exception (Phase 7 design §3): only
    `app.services.engineering_task_service` (the authenticated API
    boundary's own service) may append `GoalCreated` directly, outside
    `app/control_plane/`. Checked the same way every other "who has both
    the means and the vocabulary" boundary test in this suite is —
    confirms no OTHER module (in particular, not `ReasoningPlane` itself,
    and not the router) has both `EngineeringEventRepository` access and
    the `GOAL_CREATED` vocabulary."""
    repo_importers = {
        relative(p) for p in find_imports_of("app.repositories.engineering_event_repository")
    }
    goal_const_importers = {
        relative(p) for p in find_imports_of("app.engineering_state.events", symbol="GOAL_CREATED")
    }
    both = repo_importers & goal_const_importers
    offenders = {
        f
        for f in both
        if f != "app/services/engineering_task_service.py"
        and not f.startswith("app/engineering_state/")
        and not f.startswith("app/repositories/")
        and not f.startswith(_TEST_PATH_PREFIXES)
    }
    assert not offenders, (
        f"Unexpected set of modules with both Engineering Event append access and "
        f"the GoalCreated vocabulary: {sorted(offenders)}. Only "
        "app/services/engineering_task_service.py should have both, outside "
        "app/control_plane/'s own general event-repository access."
    )
    assert "app/services/engineering_task_service.py" in both, (
        "app/services/engineering_task_service.py no longer imports both "
        "EngineeringEventRepository and GOAL_CREATED — this test's own premise is "
        "stale; update it alongside whatever structural change caused this."
    )


# --- Phase 7.1: GET /{task_id} read-path boundary ---------------------------


def test_engineering_tasks_router_does_not_import_control_plane_directly() -> None:
    """`app/api/v1/routers/engineering_tasks.py` may (and does, for the
    `POST` handler) import `EngineeringTaskService` — but must never
    import the `ControlPlane` class itself directly. The GET handler's
    read path (`get_engineering_task`) has no need for it at all."""
    hits = find_imports_of("app.control_plane.control_plane", symbol="ControlPlane")
    offenders = {relative(p) for p in hits if relative(p) == _ENGINEERING_TASKS_ROUTER_PATH}
    assert not offenders, (
        "app/api/v1/routers/engineering_tasks.py imports ControlPlane directly — "
        "the GET read path must reach Engineering State only through "
        "EngineeringEventRepository/fold(), never construct a ControlPlane itself."
    )


def test_engineering_tasks_router_does_not_import_reasoning_plane_directly() -> None:
    hits = find_imports_of("app.reasoning_plane.plane", symbol="ReasoningPlane")
    offenders = {relative(p) for p in hits if relative(p) == _ENGINEERING_TASKS_ROUTER_PATH}
    assert not offenders, (
        "app/api/v1/routers/engineering_tasks.py imports ReasoningPlane directly — "
        "the GET read path must never construct one."
    )


def test_get_engineering_task_function_never_imports_control_plane_or_reasoning_plane() -> None:
    """AST-based, on the actual `get_engineering_task` read function's
    home module: `app/services/engineering_task_service.py` legitimately
    imports `ControlPlane`/`ReasoningPlane` for `EngineeringTaskService`
    (the POST/write path) — this test instead confirms, function-by-
    function, that `get_engineering_task` and `_build_response` (the
    functions the GET route actually calls) reference neither name
    anywhere in their own bodies."""
    import ast

    from tests.unit.architecture._source_scan import APP_ROOT

    path = APP_ROOT / "services" / "engineering_task_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    read_path_function_names = {
        "get_engineering_task",
        "list_engineering_tasks",
        "_build_response",
        "_observation_view",
    }
    forbidden_names = {"ControlPlane", "ReasoningPlane", "ToolExecutor"}

    offenders: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
            and node.name in read_path_function_names
        ):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and inner.id in forbidden_names:
                    offenders.append(f"{node.name} references {inner.id} at line {inner.lineno}")

    assert not offenders, (
        f"The GET/list read paths reference authority objects they must never touch: {offenders}."
    )


def test_engineering_tasks_router_get_handler_does_not_call_tool_executor() -> None:
    """No `ToolExecutor` symbol anywhere in the router module at all —
    the router never dispatches a Tool itself (POST delegates entirely
    to `EngineeringTaskService`, which owns the one legitimate
    `ToolExecutor` construction; GET has no Tool involvement whatsoever)."""
    hits = find_imports_of("app.tools.executor", symbol="ToolExecutor")
    offenders = {relative(p) for p in hits if relative(p) == _ENGINEERING_TASKS_ROUTER_PATH}
    assert not offenders, (
        "app/api/v1/routers/engineering_tasks.py imports ToolExecutor directly — "
        "Tool dispatch must remain exclusively inside EngineeringTaskService/ControlPlane."
    )
