"""Real-Postgres proof of the Phase 6 Replan foundation (ES §8/§10/§11):
Plan supersession, PlanStep dependency edges, PlanStep invalidation, and
the derived eligibility interaction — the durable Engineering State layer
a future Reasoning Plane will use to implement Replan (Reasoning Engine
contract §13).

Deliberately does NOT exercise any live orchestration path — none exists
(see the Phase 6 design audit's own finding: no Reasoning Plane, no live
Plan-producing code, exists anywhere in this codebase). Every scenario
below constructs its trigger conditions directly via
`EngineeringEventRepository`, exactly mirroring how
`tests/integration/test_verification_integration.py`'s
`ActionOutcomeUnknown` tests already do for the identical reason.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.control_plane import ControlPlane
from app.control_plane.model import DenialStage
from app.engineering_state import events as ev
from app.engineering_state.materialize import (
    fold,
    is_plan_step_invalidated,
    superseded_plan_event_ids,
    transitively_dependent_plan_steps,
)
from app.repositories.engineering_event_repository import EngineeringEventRepository

pytestmark = pytest.mark.asyncio


async def _goal_plan_step_chain(
    repo: EngineeringEventRepository,
    task_id: uuid.UUID,
    *,
    depends_on: list[uuid.UUID] | None = None,
) -> tuple[Any, Any, Any]:
    goal = await repo.append(
        task_id=task_id,
        event_type=ev.GOAL_CREATED,
        payload={"description": "d", "postconditions": ["p"]},
        actor="t",
    )
    plan = await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_CREATED,
        payload={"goal_event_id": str(goal.id), "scope": ["repo-a"]},
        actor="t",
        causation_event_id=goal.id,
    )
    step = await repo.append(
        task_id=task_id,
        event_type=ev.PLAN_STEP_CREATED,
        payload={
            "plan_event_id": str(plan.id),
            "description": "d",
            "postcondition": "x holds",
            "depends_on": [str(d) for d in (depends_on or [])],
        },
        actor="t",
        causation_event_id=plan.id,
    )
    return goal, plan, step


async def _record_contradiction(repo: EngineeringEventRepository, task_id: uuid.UUID) -> Any:
    return await repo.append(
        task_id=task_id,
        event_type=ev.OBSERVATION_RECORDED,
        payload={
            "raw_result": {"note": "postcondition falsified"},
            "capability": "query_knowledge_graph",
            "outcome": "completed",
            "classification": "contradiction",
        },
        actor="t",
    )


class TestFullReplanEventChain:
    """The durable chain the Phase 6 design established: Contradiction ->
    DecisionMade("replan") -> PlanStepInvalidated -> new PlanCreated
    (supersedes) -> new PlanSteps."""

    async def test_full_chain_materializes_correctly(self, db_session: AsyncSession) -> None:
        repo = EngineeringEventRepository(db_session)
        task_id = uuid.uuid4()
        goal, plan_a, step_a = await _goal_plan_step_chain(repo, task_id)

        contradiction = await _record_contradiction(repo, task_id)

        await repo.append(
            task_id=task_id,
            event_type=ev.DECISION_MADE,
            payload={
                "selected_option": "replan",
                "alternatives_considered": ["retry the same step", "escalate to human"],
                "decision_maker": "role:reasoning_engine",
            },
            actor="t",
        )

        invalidation = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(step_a.id),
                "contradiction_observation_event_id": str(contradiction.id),
                "reason": "postcondition no longer holds",
            },
            actor="t",
            causation_event_id=step_a.id,
        )

        plan_b = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_CREATED,
            payload={
                "goal_event_id": str(goal.id),
                "scope": ["repo-a"],
                "supersedes_plan_event_id": str(plan_a.id),
            },
            actor="t",
            causation_event_id=goal.id,
        )
        step_b = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_CREATED,
            payload={
                "plan_event_id": str(plan_b.id),
                "description": "corrected approach",
                "postcondition": "x holds (revised)",
            },
            actor="t",
            causation_event_id=plan_b.id,
        )

        state = fold(await repo.list_for_task(task_id))

        assert len(state.plans) == 2
        assert superseded_plan_event_ids(state) == frozenset({plan_a.id})
        assert is_plan_step_invalidated(state, step_a.id) is True
        assert len(state.decisions) == 1
        assert state.decisions[0].selected_option == "replan"
        step_b_record = next(s for s in state.plan_steps if s.event_id == step_b.id)
        assert step_b_record.postcondition == "x holds (revised)"
        assert invalidation.causation_event_id == step_a.id


class TestTransitiveInvalidationEndToEnd:
    async def test_direct_and_transitive_invalidation_via_real_appends(
        self, db_session: AsyncSession
    ) -> None:
        repo = EngineeringEventRepository(db_session)
        task_id = uuid.uuid4()
        goal, plan, step_a = await _goal_plan_step_chain(repo, task_id)
        step_b = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_CREATED,
            payload={
                "plan_event_id": str(plan.id),
                "description": "depends on A",
                "postcondition": "y holds",
                "depends_on": [str(step_a.id)],
            },
            actor="t",
            causation_event_id=plan.id,
        )
        step_c = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_CREATED,
            payload={
                "plan_event_id": str(plan.id),
                "description": "unrelated sibling",
                "postcondition": "z holds",
            },
            actor="t",
            causation_event_id=plan.id,
        )

        contradiction = await _record_contradiction(repo, task_id)
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(step_a.id),
                "contradiction_observation_event_id": str(contradiction.id),
                "reason": "falsified",
            },
            actor="t",
            causation_event_id=step_a.id,
        )

        state = fold(await repo.list_for_task(task_id))
        dependents = transitively_dependent_plan_steps(state, step_a.id)

        assert dependents == frozenset({step_b.id})
        assert step_c.id not in dependents
        # Only step_a has been DURABLY marked invalidated — propagation
        # (§11: "MUST propagate only to dependent PlanSteps") tells the
        # caller WHICH other steps need their OWN PlanStepInvalidated
        # event; it does not silently mark them via one event.
        assert is_plan_step_invalidated(state, step_a.id) is True
        assert is_plan_step_invalidated(state, step_b.id) is False
        assert is_plan_step_invalidated(state, step_c.id) is False

        # The caller (future Reasoning Plane) appends one more event per
        # transitively-affected PlanStep — proving the mechanism composes.
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(step_b.id),
                "contradiction_observation_event_id": str(contradiction.id),
                "reason": f"transitively invalidated via dependency on {step_a.id}",
            },
            actor="t",
            causation_event_id=step_b.id,
        )
        state_after = fold(await repo.list_for_task(task_id))
        assert is_plan_step_invalidated(state_after, step_b.id) is True
        assert is_plan_step_invalidated(state_after, step_c.id) is False  # still untouched


class TestEligibilityInteraction:
    """ES §11 + Cap §8 state 2: an invalidated PlanStep must not satisfy
    execution preconditions — via the EXISTING `check_eligibility`
    mechanism (Phase 3, unmodified), never a new one."""

    async def test_invalidated_plan_step_makes_a_dependent_action_ineligible(
        self, db_session: AsyncSession
    ) -> None:
        from typing import cast

        from app.capabilities.model import (
            CapabilityKind,
            CapabilityVersion,
            IsolationRequirement,
            ReversibilityClass,
            RiskClass,
            SideEffectClass,
        )
        from app.capabilities.registry import CapabilityRegistry
        from app.control_plane.model import Action, Prediction
        from app.control_plane.policy import PolicyStore
        from app.tools.executor import ToolExecutor
        from app.tools.interfaces import ToolCategory
        from app.tools.registry import ToolRegistry, ToolSpec

        repo = EngineeringEventRepository(db_session)
        task_id = uuid.uuid4()
        _, _, step = await _goal_plan_step_chain(repo, task_id)
        contradiction = await _record_contradiction(repo, task_id)
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(step.id),
                "contradiction_observation_event_id": str(contradiction.id),
                "reason": "falsified",
            },
            actor="t",
            causation_event_id=step.id,
        )

        state = fold(await repo.list_for_task(task_id))
        preconditions_hold = not is_plan_step_invalidated(state, step.id)
        assert preconditions_hold is False

        # Grant/Policy/Safety/Workspace/Verification are entirely
        # untouched by Phase 6 — `check_eligibility` needed zero code
        # changes; only the derivation of `preconditions_hold` above is
        # new. `event_repository=None` mirrors
        # `tests/unit/control_plane/test_control_plane_pipeline.py`'s own
        # established convention for a check_eligibility-only test (no
        # I/O happens inside `check_eligibility` at all).
        tool_registry = ToolRegistry()
        tool_registry.register(
            ToolSpec(
                tool_id="neo4j_graph",
                display_name="Fake",
                description="d",
                category=ToolCategory.GRAPH,
                capabilities=[],
                factory=lambda cfg: None,
                requires_auth=False,
                default_enabled=True,
            )
        )
        capability_registry = CapabilityRegistry(tool_registry=tool_registry)
        capability_registry.register(
            CapabilityVersion(
                capability_id="query_knowledge_graph",
                version=1,
                description="d",
                input_schema={"query": "str", "parameters": "dict"},
                output_schema={"data": "dict", "summary": "str", "evidence_items": "list[str]"},
                scope_ceiling="x",
                risk_class=RiskClass.LOW,
                reversibility=ReversibilityClass.REVERSIBLE,
                compensating_capability_id=None,
                external_visibility=False,
                side_effect_class=SideEffectClass.READ_ONLY,
                required_authorization="none",
                isolation_requirement=IsolationRequirement.NONE,
                execution_context_requirements=(),
                produces_artifact=False,
                tool_id="neo4j_graph",
                registered_by="test",
                kind=CapabilityKind.PRIMITIVE,
                composed_of=None,
            )
        )
        control_plane = ControlPlane(
            capability_registry=capability_registry,
            tool_executor=ToolExecutor(registry=tool_registry),
            policy_store=PolicyStore(),
            event_repository=cast(EngineeringEventRepository, None),
        )
        action = Action(
            action_id=uuid.uuid4(),
            capability_id="query_knowledge_graph",
            capability_version=1,
            parameters={"query": "x", "parameters": {}},
            prediction=Prediction(
                target_observable="summary",
                falsification_condition="x",
                evaluation_procedure="x",
                execution_context={},
                necessary_condition_rationale="x",
            ),
            plan_step_id=step.id,
        )
        eligibility = control_plane.check_eligibility(
            action,
            budget_available=True,
            lease_held=True,
            prior_action_halted=False,
            preconditions_hold=preconditions_hold,
        )

        assert eligibility.eligible is False
        assert eligibility.denial_stage == DenialStage.PRECONDITION_INVALIDATED

    async def test_a_planstep_never_invalidated_remains_eligible(
        self, db_session: AsyncSession
    ) -> None:
        repo = EngineeringEventRepository(db_session)
        task_id = uuid.uuid4()
        _, _, step = await _goal_plan_step_chain(repo, task_id)

        state = fold(await repo.list_for_task(task_id))
        assert not is_plan_step_invalidated(state, step.id)


class TestMultiplePlanVersionsAndHistoricalRetrievability:
    async def test_stale_superseded_plan_remains_historically_retrievable(
        self, db_session: AsyncSession
    ) -> None:
        repo = EngineeringEventRepository(db_session)
        task_id = uuid.uuid4()
        goal, plan_a, _ = await _goal_plan_step_chain(repo, task_id)
        plan_b = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_CREATED,
            payload={
                "goal_event_id": str(goal.id),
                "scope": ["repo-a"],
                "supersedes_plan_event_id": str(plan_a.id),
            },
            actor="t",
            causation_event_id=goal.id,
        )
        plan_c = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_CREATED,
            payload={
                "goal_event_id": str(goal.id),
                "scope": ["repo-a"],
                "supersedes_plan_event_id": str(plan_b.id),
            },
            actor="t",
            causation_event_id=goal.id,
        )

        state = fold(await repo.list_for_task(task_id))

        assert len(state.plans) == 3
        assert {p.event_id for p in state.plans} == {plan_a.id, plan_b.id, plan_c.id}
        assert superseded_plan_event_ids(state) == frozenset({plan_a.id, plan_b.id})
        plan_a_record = next(p for p in state.plans if p.event_id == plan_a.id)
        # `PlanRecord.goal_event_id` is the raw payload value (a str),
        # matching PlanRecord's own pre-existing, unchanged shape.
        assert plan_a_record.goal_event_id == str(goal.id)  # fully intact, retrievable

    async def test_a_second_independent_plan_for_the_same_goal_is_not_treated_as_superseding(
        self, db_session: AsyncSession
    ) -> None:
        """No 'latest Plan wins' shortcut — a second Plan that does NOT
        declare `supersedes_plan_event_id` leaves the first one
        un-superseded."""
        repo = EngineeringEventRepository(db_session)
        task_id = uuid.uuid4()
        goal, plan_a, _ = await _goal_plan_step_chain(repo, task_id)
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_CREATED,
            payload={"goal_event_id": str(goal.id), "scope": ["repo-b"]},
            actor="t",
            causation_event_id=goal.id,
        )

        state = fold(await repo.list_for_task(task_id))
        assert superseded_plan_event_ids(state) == frozenset()


class TestImmutability:
    async def test_committed_plan_step_invalidated_event_cannot_be_mutated(
        self, db_session: AsyncSession
    ) -> None:
        repo = EngineeringEventRepository(db_session)
        task_id = uuid.uuid4()
        _, _, step = await _goal_plan_step_chain(repo, task_id)
        contradiction = await _record_contradiction(repo, task_id)
        invalidation = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(step.id),
                "contradiction_observation_event_id": str(contradiction.id),
                "reason": "falsified",
            },
            actor="t",
            causation_event_id=step.id,
        )
        await db_session.flush()

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text(
                    "UPDATE engineering_events SET payload = "
                    "jsonb_set(payload, '{reason}', '\"tampered\"') WHERE id = :id"
                ),
                {"id": str(invalidation.id)},
            )

    async def test_original_plan_created_event_is_never_touched_by_supersession(
        self, db_session: AsyncSession
    ) -> None:
        repo = EngineeringEventRepository(db_session)
        task_id = uuid.uuid4()
        goal, plan_a, _ = await _goal_plan_step_chain(repo, task_id)
        await db_session.flush()

        events_before = await repo.list_for_task(task_id)
        plan_a_row_before = next(e for e in events_before if e.id == plan_a.id)
        payload_before = dict(plan_a_row_before.payload)

        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_CREATED,
            payload={
                "goal_event_id": str(goal.id),
                "scope": ["repo-a"],
                "supersedes_plan_event_id": str(plan_a.id),
            },
            actor="t",
            causation_event_id=goal.id,
        )

        events_after = await repo.list_for_task(task_id)
        plan_a_row_after = next(e for e in events_after if e.id == plan_a.id)
        assert plan_a_row_after.payload == payload_before


class TestConcurrentAppendBehavior:
    """Reuses Phase 1's existing per-task advisory-lock serialization —
    no new locking mechanism was added for Phase 6."""

    async def test_two_concurrent_plan_step_invalidated_appends_for_different_steps_both_succeed(
        self,
    ) -> None:
        from app.database.session import AsyncSessionLocal

        task_id = uuid.uuid4()
        async with AsyncSessionLocal() as setup_session:
            setup_repo = EngineeringEventRepository(setup_session)
            goal, plan, step_a = await _goal_plan_step_chain(setup_repo, task_id)
            step_b = await setup_repo.append(
                task_id=task_id,
                event_type=ev.PLAN_STEP_CREATED,
                payload={"plan_event_id": str(plan.id), "description": "d2", "postcondition": "y"},
                actor="t",
                causation_event_id=plan.id,
            )
            contradiction = await _record_contradiction(setup_repo, task_id)
            await setup_session.commit()

        async def _invalidate(step_id: uuid.UUID) -> None:
            async with AsyncSessionLocal() as session:
                repo = EngineeringEventRepository(session)
                await repo.append(
                    task_id=task_id,
                    event_type=ev.PLAN_STEP_INVALIDATED,
                    payload={
                        "plan_step_event_id": str(step_id),
                        "contradiction_observation_event_id": str(contradiction.id),
                        "reason": "concurrent invalidation",
                    },
                    actor="t",
                    causation_event_id=step_id,
                )
                await session.commit()

        results = await asyncio.gather(
            _invalidate(step_a.id), _invalidate(step_b.id), return_exceptions=True
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        assert not failures, f"expected both concurrent appends to succeed, got: {results}"

        async with AsyncSessionLocal() as verify_session:
            verify_repo = EngineeringEventRepository(verify_session)
            state = fold(await verify_repo.list_for_task(task_id))
            assert is_plan_step_invalidated(state, step_a.id) is True
            assert is_plan_step_invalidated(state, step_b.id) is True

    async def test_sequence_numbers_remain_unique_under_concurrent_plan_creation(self) -> None:
        """The `(task_id, sequence_number)` UNIQUE constraint backstop
        (Phase 1) applies identically to the new PlanCreated-with-
        supersedes shape — no Phase 6 code weakens it."""
        from app.database.session import AsyncSessionLocal

        task_id = uuid.uuid4()
        async with AsyncSessionLocal() as setup_session:
            setup_repo = EngineeringEventRepository(setup_session)
            goal, plan_a, _ = await _goal_plan_step_chain(setup_repo, task_id)
            await setup_session.commit()

        async def _replan() -> uuid.UUID:
            async with AsyncSessionLocal() as session:
                repo = EngineeringEventRepository(session)
                new_plan = await repo.append(
                    task_id=task_id,
                    event_type=ev.PLAN_CREATED,
                    payload={
                        "goal_event_id": str(goal.id),
                        "scope": ["repo-a"],
                        "supersedes_plan_event_id": str(plan_a.id),
                    },
                    actor="t",
                    causation_event_id=goal.id,
                )
                await session.commit()
                return new_plan.sequence_number

        seq_a, seq_b = await asyncio.gather(_replan(), _replan())
        assert seq_a != seq_b

        async with AsyncSessionLocal() as verify_session:
            verify_repo = EngineeringEventRepository(verify_session)
            events = await verify_repo.list_for_task(task_id)
            sequence_numbers = [e.sequence_number for e in events]
            assert len(sequence_numbers) == len(set(sequence_numbers))
