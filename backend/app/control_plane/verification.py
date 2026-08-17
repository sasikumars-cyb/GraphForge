"""`VerificationService` — Independent Verification, Capabilities
contract §15, Phase 5's representative slice.

Control-Plane-owned (constructed and held exclusively by `ControlPlane`,
mirroring `WorkspaceLifecycleService`'s identical ownership pattern —
see `tests/unit/architecture/test_verification_boundary.py`). Not a
Capability: it never appears in the Capability Registry, is never the
target of an `ActionProposal`, and is reached only through
`ControlPlane.request_verification`.

**No second authorization path.** This module never imports
`ToolExecutor` and never touches a Capability implementation directly —
`request_verification` below does exactly one authorization-relevant
thing: build an `Action` and hand it to
`ControlPlane._authorize_and_execute_as_verifier` (a narrow, internal
variant of `ControlPlane.authorize_and_execute`, reachable only from
this module — see that method's own docstring), which runs the SAME
pipeline every other Action goes through (Conformance -> Capability ->
Scope -> Policy -> Safety Validity -> Authorization -> Grant ->
ToolExecutor -> Tool). Independence comes from WHO is asking (a fixed,
Control-Plane-owned identity — `_VERIFIER_ACTOR`, defined and hardcoded
inside `app.control_plane.control_plane`, never passed as a parameter
by this module or any caller) and WHAT is being asked (a postcondition
resolved from the immutable event log, never a caller-supplied value),
never from a separate mechanism.

**Representative scope only** (Phase 5 design audit §9): the only
Capability exercised is `query_knowledge_graph` — read-only, no
Workspace, no repository revision, no artifact identity. A future
Workspace-requiring or artifact-producing verification target needs
additional design work explicitly deferred here, not silently assumed.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.control_plane.model import Action, Prediction
from app.engineering_state.materialize import fold

if TYPE_CHECKING:
    from app.control_plane.control_plane import ActionExecutionResult, ControlPlane
    from app.repositories.engineering_event_repository import EngineeringEventRepository

# Cap §15.2: "Distinct Role with its own Policy binding." Per the Phase 5
# design audit §5, no `Role` model exists anywhere in this codebase —
# `EngineeringEvent.actor: str` IS the concrete Role representation, by
# established convention. A distinct, fixed, Control-Plane-owned actor
# string is therefore sufficient and contract-faithful; it needs no new
# Policy dimension (Policy evaluation is actor-blind today — see
# `app.control_plane.policy`) because nothing about authorization
# depends on WHO is asking, only on WHAT is being asked, which this
# service never lets the caller influence (see `request_verification`'s
# signature: no verifier/verifier_role/verifier_actor parameter exists,
# structurally, anywhere on it).
#
# Phase 5 exit-audit correction: the actual fixed-actor CONSTANT
# (`_VERIFIER_ACTOR`) now lives in `app.control_plane.control_plane`,
# not here — this module never sees or passes an actor value at all.
# `ControlPlane._authorize_and_execute_as_verifier` (the method called
# below) hardcodes it internally with no parameter to override it; this
# service has no way to influence, and no need to know, what that
# constant's literal value is. This closes the defect the exit audit
# found: a `business_actor` parameter previously threaded through the
# PUBLIC `authorize_and_execute` was forgeable by any direct caller of
# that method, bypassing this service entirely.

# The only Capability this phase's representative Verification target
# exercises — see `app.capabilities.setup._register_query_knowledge_graph`,
# the single source of truth for this id/version pair.
_VERIFIED_CAPABILITY_ID = "query_knowledge_graph"
_VERIFIED_CAPABILITY_VERSION = 1


class VerificationTargetNotFoundError(ValueError):
    """Raised when `plan_step_event_id` does not resolve to a real,
    durably-recorded `PlanStepCreated` event in `task_id`'s history.
    Verification has nothing to evaluate without one — this is a
    distinct, named failure, never silently treated as "nothing to
    verify, succeed trivially"."""


class VerificationService:
    """One instance per `ControlPlane` — constructed and held exclusively
    by it (see `ControlPlane.__init__`), never independently
    constructed elsewhere (structurally enforced by
    `tests/unit/architecture/test_verification_boundary.py`, the same
    discipline `test_workspace_authority_boundary.py` already applies to
    `WorkspaceLifecycleService`)."""

    def __init__(
        self, *, control_plane: "ControlPlane", event_repository: "EngineeringEventRepository"
    ) -> None:
        self._control_plane = control_plane
        self._events = event_repository

    async def request_verification(
        self, *, task_id: uuid.UUID, plan_step_event_id: uuid.UUID
    ) -> "ActionExecutionResult":
        """Cap §15: independently verify one PlanStep's pinned
        postcondition.

        Accepts ONLY `task_id` and `plan_step_event_id` — no
        `postcondition`, `prediction`, `verifier_actor`,
        `artifact_identity`, or `repository_revision` parameter exists
        on this signature (Phase 5 design audit §11), which is what
        makes "a caller substitutes a different postcondition" or "a
        caller selects/configures the verifier" structurally impossible
        through this API, not merely policy that happens not to be
        violated.

        Any caller may request that verification occur (Phase 5 design
        audit §13) — the request carries no influence over verifier
        identity or Capability invocation, so restricting WHO may call
        this method would add no real independence.
        """
        events = await self._events.list_for_task(task_id)
        state = fold(events)

        plan_step = next(
            (step for step in state.plan_steps if step.event_id == plan_step_event_id), None
        )
        if plan_step is None:
            raise VerificationTargetNotFoundError(
                f"No PlanStepCreated event {plan_step_event_id} found for task {task_id} — "
                "Verification has nothing to evaluate."
            )

        postcondition = plan_step.postcondition

        # Cap §13's Prediction requirements 1-4 (mechanically checked at
        # Conformance) apply to this Prediction exactly as they would to
        # any other Action's — Verification is not exempt from
        # Conformance. `target_observable="summary"` is
        # `query_knowledge_graph`'s only always-populated output field
        # (see `app.capabilities.setup`'s `output_schema`); using it as
        # the observable is this phase's minimal, deterministic
        # evaluation, not a general postcondition-evaluation engine.
        prediction = Prediction(
            target_observable="summary",
            falsification_condition=(
                f"query_knowledge_graph returns an empty summary for postcondition: "
                f"{postcondition!r}"
            ),
            evaluation_procedure=(
                "independently query the knowledge graph using the pinned postcondition "
                "text and evaluate whether a non-empty summary was returned"
            ),
            execution_context={},
            necessary_condition_rationale=(
                f"independently verifies PlanStep {plan_step_event_id}'s pinned postcondition"
            ),
        )
        action = Action(
            action_id=uuid.uuid4(),
            capability_id=_VERIFIED_CAPABILITY_ID,
            capability_version=_VERIFIED_CAPABILITY_VERSION,
            parameters={"query": postcondition, "parameters": {}},
            prediction=prediction,
            plan_step_id=plan_step_event_id,
        )

        # `_authorize_and_execute_as_verifier` — NOT the public
        # `authorize_and_execute` — the only method that ever records the
        # fixed verifier actor. This service supplies no identity value
        # here at all; the method hardcodes it internally (Phase 5
        # exit-audit correction).
        return await self._control_plane._authorize_and_execute_as_verifier(
            task_id=task_id,
            action=action,
            human_approval=None,
        )


__all__ = ["VerificationService", "VerificationTargetNotFoundError"]
