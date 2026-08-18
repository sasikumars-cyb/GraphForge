"""Phase 5 guardrail — the Control Plane is the sole Independent
Verification authority (Cap §15), and the verifier receives none of the
generator's Reasoning-Plane state.

`VerificationService` is an implementation component, not a second
authority — mirrors `test_workspace_authority_boundary.py`'s exact
pattern and rationale, applied to Verification instead of Workspace.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.control_plane.control_plane import ControlPlane
from app.control_plane.verification import VerificationService
from tests.unit.architecture._source_scan import find_imports_of, relative

_INTERNAL_TO_CONTROL_PLANE_PREFIX = "app/control_plane/"
_TEST_PATH_PREFIXES: tuple[str, ...] = ("tests/",)

_ALLOWED_VERIFICATION_SERVICE_IMPORTERS: frozenset[str] = frozenset()


def test_verification_service_is_not_imported_outside_the_control_plane() -> None:
    hits = find_imports_of("app.control_plane.verification", symbol="VerificationService")
    offenders = {
        relative(path)
        for path in hits
        if not relative(path).startswith(_INTERNAL_TO_CONTROL_PLANE_PREFIX)
        and not relative(path).startswith(_TEST_PATH_PREFIXES)
        and relative(path) not in _ALLOWED_VERIFICATION_SERVICE_IMPORTERS
    }
    assert not offenders, (
        f"VerificationService imported outside app/control_plane/: {sorted(offenders)}. "
        "Cap §15: the Control Plane is the sole authority over Independent "
        "Verification — nothing Reasoning-Plane-adjacent may construct or "
        "call this service directly."
    )


def test_verification_module_does_not_import_belief_or_hypothesis_state() -> None:
    """Cap §15.2: the verifier must not receive generator Beliefs,
    Hypotheses, confidence, rationale, or narrative — checked
    structurally: `app.control_plane.verification` must not import
    `BeliefRecord` (the only Engineering-State belief-shaped record that
    exists in this codebase; no `HypothesisRecord` exists at all, see
    the Phase 5 design audit §8's terminology note)."""
    hits = find_imports_of("app.engineering_state.materialize", symbol="BeliefRecord")
    offenders = {relative(path) for path in hits if "verification" in relative(path)}
    assert not offenders, (
        f"app/control_plane/verification.py imports BeliefRecord: {sorted(offenders)}. "
        "Cap §15.2: the verifier must never receive the generator's Beliefs."
    )


def test_verification_module_does_not_import_tool_executor_or_capability_implementations() -> None:
    """Cap §15: 'Do not bypass authorization' / 'Do not directly invoke
    ToolExecutor' / 'Do not directly invoke Capability implementation' —
    Verification must reach a Tool only through
    `ControlPlane.authorize_and_execute`, the same pipeline every other
    Action goes through."""
    tool_executor_hits = find_imports_of("app.tools.executor", symbol="ToolExecutor")
    offenders = {relative(path) for path in tool_executor_hits if "verification" in relative(path)}
    assert not offenders, (
        f"app/control_plane/verification.py imports ToolExecutor directly: "
        f"{sorted(offenders)}. Verification must dispatch only through "
        "ControlPlane.authorize_and_execute — no second authorization path."
    )


def test_request_verification_signature_has_no_verifier_selection_parameter() -> None:
    """Cap §15.2: 'the generator cannot select verifier.' Structurally
    checked, not merely by convention: `request_verification` must not
    accept any parameter that would let a caller influence WHICH actor
    verifies, WHAT postcondition is evaluated, or WHICH artifact/revision
    is bound — Phase 5 instructions' own explicit list."""
    signature = inspect.signature(VerificationService.request_verification)
    forbidden_params = {
        "verifier",
        "verifier_role",
        "verifier_actor",
        "postcondition",
        "prediction",
        "artifact_identity",
        "repository_revision",
    }
    offending = forbidden_params & set(signature.parameters)
    assert not offending, (
        f"VerificationService.request_verification accepts caller-controlled "
        f"parameter(s) {sorted(offending)} — Cap §15.2 requires verifier "
        "identity and postcondition to be structurally fixed/resolved, "
        "never caller-supplied."
    )
    assert set(signature.parameters) - {"self"} == {"task_id", "plan_step_event_id"}, (
        "VerificationService.request_verification's signature has drifted from "
        "the approved Phase 5 minimal API — only task_id and plan_step_event_id "
        "are permitted."
    )


def test_authorize_and_execute_public_signature_has_no_actor_override_parameter() -> None:
    """Phase 5 exit-audit correction, requirement 1 — the PUBLIC
    `authorize_and_execute` must never accept a `business_actor`/
    `verifier_actor`/`verifier_role` (or any other actor-shaped)
    parameter. This is the structural half of the fix for the exit
    audit's finding: any caller who could pass an arbitrary actor
    through this method could forge the reserved verifier identity on
    an ordinary Action, bypassing `VerificationService` entirely."""
    signature = inspect.signature(ControlPlane.authorize_and_execute)
    forbidden_params = {"business_actor", "verifier_actor", "verifier_role", "actor"}
    offending = forbidden_params & set(signature.parameters)
    assert not offending, (
        f"ControlPlane.authorize_and_execute accepts actor-shaped parameter(s) "
        f"{sorted(offending)} — this is exactly the hole the Phase 5 exit audit "
        "found and required removed. The public signature must match Phase 3's "
        "original shape exactly: task_id, action, human_approval, "
        "lease_conflicts, emergency_policy_active — nothing else."
    )
    assert set(signature.parameters) - {"self"} == {
        "task_id",
        "action",
        "human_approval",
        "lease_conflicts",
        "emergency_policy_active",
    }, (
        "ControlPlane.authorize_and_execute's public signature has drifted from "
        "its Phase 3 original shape."
    )


def test_verifier_actor_constant_is_referenced_from_exactly_one_call_site() -> None:
    """Phase 5 exit-audit correction, requirement 6 — there must be
    exactly ONE production code path capable of recording
    `_VERIFIER_ACTOR` on an Observation. AST-checked directly against
    `control_plane.py`'s source: every function/method definition whose
    body references the `_VERIFIER_ACTOR` name, anywhere in the module.
    A second reference site would mean a second way to forge the
    verifier identity — exactly the class of defect this correction
    closes."""
    source_path = (
        Path(__file__).resolve().parents[3] / "app" / "control_plane" / "control_plane.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

    referencing_functions: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and inner.id == "_VERIFIER_ACTOR":
                    referencing_functions.append(node.name)
                    break

    assert referencing_functions == ["_authorize_and_execute_as_verifier"], (
        f"_VERIFIER_ACTOR is referenced from {referencing_functions} in "
        "control_plane.py — expected exactly ['_authorize_and_execute_as_verifier']. "
        "Any other reference site is a second path capable of forging the "
        "verifier identity, which is the exact class of defect the Phase 5 "
        "exit-audit correction closes."
    )
