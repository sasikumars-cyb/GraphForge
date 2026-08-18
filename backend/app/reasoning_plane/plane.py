"""`ReasoningPlane` — Reasoning Engine contract §5, Phase 7's minimal
implementation.

**Deliberately minimal — architectural proof, not intelligence.** No
Hypothesis engine, no candidate ranking, no multi-step planning, no
autonomy levels, no multi-agent reasoning. Exactly one fixed,
deterministic rule: given a task whose Engineering State already
contains a `GoalCreated` event, produce exactly one `PlanCreated`,
exactly one `PlanStepCreated`, and return exactly one transient
`ActionProposal` targeting the one registered representative Capability
(`query_knowledge_graph`).

**Non-responsibilities, structurally enforced** (Reasoning Engine
contract §2, mirrored exactly for this minimal implementation — see
`tests/unit/architecture/test_reasoning_plane_boundary.py`):

- Never executes a Tool — this module never imports `ToolExecutor`.
- Never authorizes itself — this module never imports `AuthorizationGrant`
  or anything from `app.control_plane.grant`.
- Never issues a Grant, never touches Policy or Safety Validity.
- Never creates or touches a Workspace — never imports
  `WorkspaceLifecycleService`.
- Never requests or performs Verification — never imports
  `VerificationService`.
- Never appends an `Authorization*`/`Workspace*`/`ObservationRecorded`
  event — the only event types this module's `EngineeringEventRepository`
  usage ever appends are `PlanCreated`/`PlanStepCreated`, mirroring
  `ControlPlane`'s own "only I may append Authorization*/Workspace*"
  ownership pattern, applied here to Plan/PlanStep authorship instead.
- Never creates `GoalCreated` — that is the authenticated API boundary's
  job (see `app.services.engineering_task_service`), never this module's.

The `ActionProposal` this returns is deliberately transient — never
itself durably recorded as an event (see the Phase 7 design's own
reasoning: only the authorization *outcome*, not the proposal, is
Engineering State's concern; ES's actual closed vocabulary has never
included an `ActionProposed` event type, and this phase does not invent
one).
"""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane.model import Action, ActionProposal, Prediction
from app.engineering_state.events import PLAN_CREATED, PLAN_STEP_CREATED
from app.engineering_state.materialize import fold
from app.repositories.engineering_event_repository import EngineeringEventRepository

# The only Capability this minimal Reasoning Plane ever targets — see
# `app.capabilities.setup._register_query_knowledge_graph`, the single
# source of truth for this id/version pair. Fixed, not selected via any
# candidate-ranking logic (none exists in this minimal implementation).
_TARGET_CAPABILITY_ID = "query_knowledge_graph"
_TARGET_CAPABILITY_VERSION = 1

_REASONING_PLANE_ACTOR = "reasoning_plane"


class ReasoningPlaneError(ValueError):
    """Raised when `run()` cannot proceed — e.g. no `GoalCreated` event
    exists yet for the given task. A distinct, named failure, never a
    silently-fabricated Goal."""


class ReasoningPlane:
    """One instance per request/task context — mirrors `ControlPlane`'s
    own identical construction convention. Holds no cross-request state;
    every fact it needs is read fresh from `event_repository` each call.
    """

    def __init__(self, *, event_repository: EngineeringEventRepository) -> None:
        self._events = event_repository

    async def run(
        self,
        *,
        task_id: uuid.UUID,
        capability_parameters: dict[str, Any] | None = None,
    ) -> ActionProposal:
        """Reads materialized Engineering State for `task_id` (requires
        an existing `GoalCreated`), durably appends one `PlanCreated` and
        one `PlanStepCreated`, and returns a transient `ActionProposal`
        the caller (never this method) hands to
        `ControlPlane.check_conformance`/`authorize_and_execute`.

        `capability_parameters` is passed through, opaque, into the
        constructed Action's nested `parameters` dict — this module does
        not interpret or require anything about its shape (keeping it
        Capability-agnostic); the caller supplies whatever the target
        Tool actually needs (e.g. a real `neo4j_graph` invocation needs
        `db`/`user_id`; the fake tool used throughout this repository's
        own test suite needs nothing).
        """
        events = await self._events.list_for_task(task_id)
        state = fold(events)
        if state.goal is None:
            raise ReasoningPlaneError(
                f"No GoalCreated event exists for task {task_id} — Reasoning Plane has "
                "nothing to plan from."
            )

        plan_event = await self._events.append(
            task_id=task_id,
            event_type=PLAN_CREATED,
            payload={"goal_event_id": str(state.goal.event_id), "scope": []},
            actor=_REASONING_PLANE_ACTOR,
            causation_event_id=state.goal.event_id,
        )

        # The single, fixed rule: the PlanStep's postcondition is simply
        # "the representative Capability returns evidence for the Goal's
        # own description" — deliberately not more sophisticated; this is
        # the smallest real postcondition that makes Verification (Phase
        # 5, unmodified) meaningful.
        postcondition = (
            f"{_TARGET_CAPABILITY_ID} returns a non-empty summary for: "
            f"{state.goal.description}"
        )
        plan_step_event = await self._events.append(
            task_id=task_id,
            event_type=PLAN_STEP_CREATED,
            payload={
                "plan_event_id": str(plan_event.id),
                "description": state.goal.description,
                "postcondition": postcondition,
            },
            actor=_REASONING_PLANE_ACTOR,
            causation_event_id=plan_event.id,
        )

        prediction = Prediction(
            target_observable="summary",
            falsification_condition=(
                f"{_TARGET_CAPABILITY_ID} returns an empty summary for: "
                f"{state.goal.description!r}"
            ),
            evaluation_procedure=(
                "query the knowledge graph using the Goal's own description text and "
                "evaluate whether a non-empty summary was returned"
            ),
            execution_context={},
            necessary_condition_rationale="directly answers the Goal's own description",
        )
        action = Action(
            action_id=uuid.uuid4(),
            capability_id=_TARGET_CAPABILITY_ID,
            capability_version=_TARGET_CAPABILITY_VERSION,
            parameters={
                "query": state.goal.description,
                "parameters": dict(capability_parameters or {}),
            },
            prediction=prediction,
            plan_step_id=plan_step_event.id,
        )

        return ActionProposal(
            proposal_id=uuid.uuid4(),
            task_id=task_id,
            goal_id=state.goal.event_id,
            proposing_role=_REASONING_PLANE_ACTOR,
            actions=(action,),
            engineering_state_snapshot_event_id=events[-1].id,
        )


__all__ = ["ReasoningPlane", "ReasoningPlaneError"]
