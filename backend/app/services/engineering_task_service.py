"""`EngineeringTaskService` — Phase 7's minimal end-to-end integration,
plus the Phase 7.1 read-only visibility slice and Phase 7.2's
productization (list view).

**Orchestration only.** `EngineeringTaskService` sequences already-existing,
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

**`get_engineering_task` (Phase 7.1) and `list_engineering_tasks` (Phase
7.2) are genuinely separate, PURE READ paths** — module-level functions,
not methods on `EngineeringTaskService`, deliberately: they need (and
import) only `EngineeringEventRepository` and `fold()`. Neither ever
constructs a `ControlPlane`, a `ReasoningPlane`, a `CapabilityRegistry`,
or a `PolicyStore` — there is no `ControlPlane`/`ReasoningPlane`
reference anywhere in scope when either function runs, not merely
"unused." Neither ever appends an event or calls `.commit()`.

`list_engineering_tasks` reuses Phase 4's `list_by_event_types` (already
existing, cross-task-scoped repository method — no new repository method
was added for this) to find every task's `GoalCreated` event, then folds
each task's own stream through the identical `_build_response` the
detail `GET` already uses, projected down to `EngineeringTaskSummary`.
This is O(N tasks) additional `list_for_task` queries — acceptable for
this minimal productization slice's expected task volumes, and
deliberately NOT a new database projection (no new table, no
materialized view) per the Phase 7.2 instruction; revisit only if this
becomes a real bottleneck.
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
from app.core.redact import redact_secrets
from app.engineering_state.events import GOAL_CREATED, PLAN_CREATED
from app.engineering_state.materialize import (
    MaterializedEngineeringState,
    ObservationRecord,
    fold,
    has_unresolved_outcome_unknown,
    is_plan_step_invalidated,
)
from app.models.engineering_event import EngineeringEvent
from app.reasoning_plane.plane import ReasoningPlane
from app.repositories.engineering_event_repository import EngineeringEventRepository
from app.schemas.engineering_task import (
    EngineeringTaskGoal,
    EngineeringTaskObservation,
    EngineeringTaskPlanStep,
    EngineeringTaskResponse,
    EngineeringTaskSummary,
)
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
    per-request construction convention exactly. Used ONLY by the
    `POST` (create-and-execute) path — see `get_engineering_task` below
    for the separate, ControlPlane-free `GET` path."""

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
        await self._events.append(
            task_id=task_id,
            event_type=GOAL_CREATED,
            payload={
                "description": description,
                "postconditions": postconditions,
                # Phase 7.3 (ownership fix): `user_id` was already
                # received as this method's own parameter but, until now,
                # never persisted — see the ownership design audit. This
                # is the only place a new task's owner is ever recorded.
                "user_id": str(user_id),
            },
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
            await self._control_plane.authorize_and_execute(
                task_id=task_id, action=action, human_approval=None
            )
        except (AuthorizationDeniedError, CapabilityGapError) as exc:
            raise ForbiddenError(str(exc), error_code="engineering_task_denied") from exc

        await self._control_plane.request_verification(
            task_id=task_id, plan_step_event_id=action.plan_step_id
        )

        await self._db.commit()

        events = await self._events.list_for_task(task_id)
        return _build_response(task_id, events)


def _is_owned_by(state: MaterializedEngineeringState, requesting_user_id: uuid.UUID) -> bool:
    """Phase 7.3 (ownership fix) — the SINGLE ownership rule shared by
    both `get_engineering_task` and `list_engineering_tasks`, so the two
    views can never disagree about who may see a task (Ownership Design
    Audit §8, "the SAME ownership rule").

    `state.goal.user_id is None` (a task created before this field
    existed) is deliberately treated as NOT owned by anyone — never
    "visible to all" (that was the legacy Workflow behavior, explicitly
    rejected for this fix; see the audit's §4). This mirrors
    `CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md` §20.3's own fail-closed
    convention: "Unreadable or unresolvable Policy = deny; unavailable
    Policy is never permissive." Unresolvable ownership = deny, the same
    way. No admin bypass — see the audit's §5; this function does not
    even receive a role, by design, so one cannot accidentally be added
    without a deliberate signature change.
    """
    return state.goal is not None and state.goal.user_id == requesting_user_id


async def get_engineering_task(
    *, db: AsyncSession, task_id: uuid.UUID, requesting_user_id: uuid.UUID
) -> EngineeringTaskResponse | None:
    """Phase 7.1 — the entire read path, ownership-checked as of Phase
    7.3. Constructs only `EngineeringEventRepository`; never a
    `ControlPlane`, never a `ReasoningPlane`, never a Capability/Policy/
    Tool registry. Returns `None` both when no Engineering State exists
    for `task_id` AND when it exists but isn't owned by
    `requesting_user_id` — the router translates either case into an
    identical 404, deliberately never distinguishing "doesn't exist" from
    "exists, but isn't yours" (Ownership Design Audit §8: no disclosure).

    Never appends an event, never calls `.commit()` — a plain read of
    already-durable state via the existing, unmodified `list_for_task`/
    `fold()` mechanism.
    """
    events = await EngineeringEventRepository(db).list_for_task(task_id)
    if not events:
        return None
    if not _is_owned_by(fold(events), requesting_user_id):
        return None
    return _build_response(task_id, events)


async def list_engineering_tasks(
    *, db: AsyncSession, requesting_user_id: uuid.UUID
) -> list[EngineeringTaskSummary]:
    """Phase 7.2 — the entire list-view read path. Constructs only
    `EngineeringEventRepository`; never a `ControlPlane`, never a
    `ReasoningPlane`, never a Capability/Policy/Tool registry.

    Every task is identified by its `GoalCreated` event (exactly one per
    task, always the first event in that task's stream — see
    `EngineeringEventRepository.append`'s causal-order enforcement).
    `list_by_event_types` (Phase 4, unmodified) already provides the
    one genuinely new query shape this needs — no repository change was
    required for this endpoint.

    Newest-created first. Ties (equal `created_at`) are broken by
    `task_id` alone — `recorded_at`'s column default is `func.now()`,
    Postgres's TRANSACTION-START time, not statement time, so two
    requests whose transactions begin within the same instant can share
    an identical `recorded_at`; Engineering State records nothing else
    that would meaningfully order them. This is a deliberate, honest
    choice: deterministic and reproducible across calls, but it does NOT
    claim to know which of two same-instant tasks came "first" — because
    the durable state genuinely doesn't say. Fixing the underlying
    timestamp granularity would mean changing `EngineeringEvent`'s column
    default, a real Engineering State domain-model change and out of
    scope for this productization-only phase.

    Ownership-filtered as of Phase 7.3 (`_is_owned_by`, the identical
    rule `get_engineering_task` uses) — only tasks owned by
    `requesting_user_id` are returned; other users' tasks and
    unowned/historical tasks are silently omitted, never a 403 or any
    other signal that they exist.

    Never appends an event, never calls `.commit()`.
    """
    repo = EngineeringEventRepository(db)
    goal_events = await repo.list_by_event_types(frozenset({GOAL_CREATED}))

    summaries: list[EngineeringTaskSummary] = []
    for goal_event in goal_events:
        events = await repo.list_for_task(goal_event.task_id)
        state = fold(events)
        if not _is_owned_by(state, requesting_user_id):
            continue
        response = _build_response(goal_event.task_id, events)
        summaries.append(
            EngineeringTaskSummary(
                task_id=response.task_id,
                created_at=response.created_at,
                updated_at=max(e.recorded_at for e in events),
                description=response.goal.description,
                classification=response.verifier_observation.classification,
            )
        )

    summaries.sort(key=lambda s: (s.created_at, str(s.task_id)), reverse=True)
    return summaries


def _build_response(task_id: uuid.UUID, events: list[EngineeringEvent]) -> EngineeringTaskResponse:
    """Shared by both `EngineeringTaskService.create_and_execute` (POST)
    and `get_engineering_task` (GET) — the two views of one task can
    never drift, because both are built from the identical materialized
    state via the identical function. Deliberately tolerant of a
    Verification-less/Observation-less state (defaults to `None`/empty
    sub-objects) rather than raising, since a GET may in principle be
    called against Engineering State that isn't the full expected shape.
    """
    state: MaterializedEngineeringState = fold(events)
    goal_event = next(e for e in events if e.event_type == GOAL_CREATED)
    plan_event = next(e for e in events if e.event_type == PLAN_CREATED)

    assert state.goal is not None  # guaranteed: goal_event above already proves one exists
    goal = EngineeringTaskGoal(
        description=state.goal.description, postconditions=list(state.goal.postconditions)
    )

    plan_step = state.plan_steps[0] if state.plan_steps else None
    plan_step_view = (
        EngineeringTaskPlanStep(
            event_id=plan_step.event_id,
            description=plan_step.description,
            postcondition=plan_step.postcondition,
            invalidated=plan_step.invalidated,
        )
        if plan_step is not None
        else None
    )

    observations = list(state.observations)
    generator_observation = next((o for o in observations if o.actor is None), None)
    verifier_observation = next(
        (o for o in observations if o.actor == "control_plane_verifier"), None
    )

    return EngineeringTaskResponse(
        task_id=task_id,
        created_at=goal_event.recorded_at,
        goal_event_id=goal_event.id,
        goal=goal,
        plan_event_id=plan_event.id,
        plan_step_event_id=plan_step.event_id if plan_step is not None else plan_event.id,
        plan_step=plan_step_view,
        generator_observation=_observation_view(generator_observation),
        verifier_observation=_observation_view(verifier_observation),
    )


def _observation_view(observation: ObservationRecord | None) -> EngineeringTaskObservation:
    if observation is None:
        return EngineeringTaskObservation(
            success=None,
            outcome=None,
            classification=None,
            actor=None,
            summary=None,
            error=None,
            capability=None,
        )
    raw_result = observation.raw_result if isinstance(observation.raw_result, dict) else {}
    success = raw_result.get("success")
    # Phase 8 (Observation/Evidence Detail Surfacing): `summary`/`error`
    # are already durably recorded on `raw_result` (Phase 1's original
    # shape, `app.control_plane.control_plane`'s ObservationRecorded
    # append) — never fetched live, never fabricated. Redacted through
    # the SAME, already-established mechanism used elsewhere for
    # LLM-prompt/Evidence text (`app.core.redact.redact_secrets`) —
    # known credential/token-shaped patterns are redacted; this is not a
    # claim of perfect semantic secrecy (Phase 8 Design Audit §4).
    # `.get(...)` on a dict returns `None` for a historical Observation
    # recorded before this field existed — `redact_secrets(None)` would
    # fail, so redaction is applied only when a value is actually present.
    raw_summary = raw_result.get("summary")
    raw_error = raw_result.get("error")
    summary = redact_secrets(raw_summary) if raw_summary is not None else None
    error = redact_secrets(raw_error) if raw_error is not None else None
    return EngineeringTaskObservation(
        success=success,
        outcome=observation.outcome,
        classification=observation.classification,
        actor=observation.actor,
        summary=summary,
        error=error,
        capability=observation.capability,
    )


__all__ = [
    "EngineeringTaskCreationFailedError",
    "EngineeringTaskService",
    "get_engineering_task",
    "list_engineering_tasks",
]
