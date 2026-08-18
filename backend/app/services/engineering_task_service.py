"""`EngineeringTaskService` — Phase 7's minimal end-to-end integration.

**Orchestration only.** This service sequences already-existing,
already-audited components; it re-implements none of their
responsibilities:

    Goal creation (this service, the narrow approved exception)
        -> ReasoningPlane.run()                 [Phase 7, new]
        -> ControlPlane.check_conformance()      [Phase 3, unmodified]
        -> ControlPlane.check_eligibility()      [Phase 3, unmodified]
        -> ControlPlane.authorize_and_execute()  [Phase 3/5, unmodified]
        -> ControlPlane.request_verification()   [Phase 5, unmodified]

Authorization, Policy, Safety, Grant issuance/consumption, Tool dispatch,
Observation classification, verifier identity, and Workspace lifecycle
all remain exactly where Phases 3-6 put them — this module owns none of
them and duplicates none of their logic. If any of those responsibilities
appeared to need reimplementing here, that would be a stop-and-report
condition; none did.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.registry import CapabilityRegistry
from app.control_plane.control_plane import (
    AuthorizationDeniedError,
    CapabilityGapError,
    ControlPlane,
)
from app.control_plane.policy import PolicyStore
from app.core.exceptions import AppError, ForbiddenError
from app.engineering_state.events import GOAL_CREATED, PLAN_CREATED
from app.engineering_state.materialize import (
    fold,
    has_unresolved_outcome_unknown,
    is_plan_step_invalidated,
)
from app.models.engineering_event import EngineeringEvent
from app.reasoning_plane.plane import ReasoningPlane
from app.repositories.engineering_event_repository import EngineeringEventRepository
from app.schemas.engineering_task import EngineeringTaskObservation, EngineeringTaskResponse
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, get_tool_registry

_API_BOUNDARY_ACTOR = "api:engineering_tasks"


class EngineeringTaskCreationFailedError(AppError):
    """A genuine internal invariant violation (e.g. `ReasoningPlane`
    reporting no Goal exists immediately after this service just created
    one) — never expected to be user-triggerable; a 500, not a denial."""

    status_code = 500
    error_code = "engineering_task_creation_failed"


class EngineeringTaskService:
    """One instance per request — mirrors `ControlPlane`'s own
    per-request construction convention exactly."""

    def __init__(
        self,
        *,
        db: AsyncSession,
        capability_registry: CapabilityRegistry,
        policy_store: PolicyStore,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._db = db
        self._events = EngineeringEventRepository(db)
        self._control_plane = ControlPlane(
            capability_registry=capability_registry,
            tool_executor=ToolExecutor(registry=tool_registry or get_tool_registry()),
            policy_store=policy_store,
            event_repository=self._events,
        )
        self._reasoning_plane = ReasoningPlane(event_repository=self._events)

    async def create_and_execute(
        self,
        *,
        description: str,
        postconditions: list[str],
        user_id: uuid.UUID,
    ) -> EngineeringTaskResponse:
        task_id = uuid.uuid4()

        # The one, deliberately narrow exception (Phase 7 design §3):
        # the authenticated API boundary itself appends GoalCreated —
        # never the Reasoning Plane, never a caller-supplied arbitrary
        # event. `get_current_user` has already verified this is a real
        # human before this method is ever called.
        goal_event = await self._events.append(
            task_id=task_id,
            event_type=GOAL_CREATED,
            payload={"description": description, "postconditions": postconditions},
            actor=_API_BOUNDARY_ACTOR,
        )

        try:
            # Deliberately no `capability_parameters` here: `Action.
            # parameters` is hashed durably by Phase 3's
            # `hash_action_parameters` (Grant identity binding,
            # unmodified) — it must stay JSON-serializable. A live
            # `AsyncSession` (what the REAL `neo4j_graph` Tool needs at
            # dispatch time) cannot flow through it. The fake Tool this
            # phase's own test suite uses (matching every prior phase's
            # precedent) needs nothing here. Wiring the real Tool's
            # runtime `db`/`user_id` requirement through a non-hashed
            # channel is explicitly out of this phase's scope — see the
            # exit audit's own named limitation.
            proposal = await self._reasoning_plane.run(task_id=task_id)
        except Exception as exc:  # ReasoningPlaneError, structurally unreachable here
            raise EngineeringTaskCreationFailedError(str(exc)) from exc

        conformance = self._control_plane.check_conformance(proposal)
        if not conformance.conformant:
            raise ForbiddenError(
                conformance.denial_reason or "proposal not conformant",
                error_code="engineering_task_not_conformant",
            )

        # Exactly one Action in this minimal slice (Reasoning Engine
        # contract §5 keeps composition trivial by design here).
        action = proposal.actions[0]

        state = fold(await self._events.list_for_task(task_id))
        preconditions_hold = not (
            has_unresolved_outcome_unknown(state)
            or is_plan_step_invalidated(state, action.plan_step_id)
        )
        eligibility = self._control_plane.check_eligibility(
            action,
            budget_available=True,
            lease_held=True,
            prior_action_halted=False,
            preconditions_hold=preconditions_hold,
        )
        if not eligibility.eligible:
            raise ForbiddenError(
                eligibility.denial_reason or "action not eligible",
                error_code="engineering_task_not_eligible",
            )

        try:
            generator_result = await self._control_plane.authorize_and_execute(
                task_id=task_id, action=action, human_approval=None
            )
        except (AuthorizationDeniedError, CapabilityGapError) as exc:
            raise ForbiddenError(str(exc), error_code="engineering_task_denied") from exc

        verifier_result = await self._control_plane.request_verification(
            task_id=task_id, plan_step_event_id=action.plan_step_id
        )

        await self._db.commit()

        events = await self._events.list_for_task(task_id)
        plan_event_id = next(e.id for e in events if e.event_type == PLAN_CREATED)

        return EngineeringTaskResponse(
            task_id=task_id,
            goal_event_id=goal_event.id,
            plan_event_id=plan_event_id,
            plan_step_event_id=action.plan_step_id,
            generator_observation=_observation_view(
                events, generator_result.observation_event_id
            ),
            verifier_observation=_observation_view(events, verifier_result.observation_event_id),
        )


def _observation_view(
    events: list[EngineeringEvent], observation_event_id: uuid.UUID
) -> EngineeringTaskObservation:
    event = next(e for e in events if e.id == observation_event_id)
    payload = event.payload
    return EngineeringTaskObservation(
        success=payload.get("success"),
        classification=payload.get("classification"),
        actor=payload.get("actor"),
    )


__all__ = ["EngineeringTaskCreationFailedError", "EngineeringTaskService"]
