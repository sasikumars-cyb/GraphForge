"""Real-Postgres proof of the Phase 5 Independent Verification path
(Cap §15): `ControlPlane.request_verification` -> `VerificationService`
-> the SAME `authorize_and_execute` pipeline every other Action goes
through -> `query_knowledge_graph` -> a durably classified
`ObservationRecorded` event.

Also covers the Observation-classification fields these events now
carry (Cap §16.2) and the `ActionOutcomeUnknown` dependent-Action
blocking derivation (Cap §16.2 step 2 / ES §9), since both reach
Engineering State through the exact same `authorize_and_execute` path
this file already exercises.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.model import (
    CapabilityKind,
    CapabilityVersion,
    IsolationRequirement,
    ReversibilityClass,
    RiskClass,
    SideEffectClass,
)
from app.capabilities.registry import CapabilityRegistry
from app.control_plane.control_plane import _VERIFIER_ACTOR, ControlPlane
from app.control_plane.model import Action, DenialStage, Prediction
from app.control_plane.policy import (
    PolicyRule,
    PolicyRuleEffect,
    PolicyScopeLevel,
    PolicyStore,
    PolicyVersion,
    seed_system_policy_allowing,
)
from app.control_plane.verification import VerificationTargetNotFoundError
from app.engineering_state import events as ev
from app.engineering_state.materialize import fold, has_unresolved_outcome_unknown
from app.repositories.engineering_event_repository import EngineeringEventRepository
from app.tools.executor import ToolExecutor
from app.tools.interfaces import ToolCategory, ToolHealth, ToolInput, ToolResult
from app.tools.registry import ToolRegistry, ToolSpec

pytestmark = pytest.mark.asyncio


class _FakeGraphTool:
    tool_id = "neo4j_graph"
    display_name = "Fake Graph"
    description = "d"
    category = ToolCategory.GRAPH
    capabilities: list[str] = []

    def __init__(self, config: dict[str, Any]) -> None:
        pass

    async def execute(self, input: ToolInput) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={"data": {"repositories": []}, "summary": f"result for: {input.query}"},
        )

    async def health_check(self) -> ToolHealth:
        return ToolHealth.HEALTHY

    def requires_auth(self) -> bool:
        return False


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            tool_id="neo4j_graph",
            display_name="Fake Graph",
            description="d",
            category=ToolCategory.GRAPH,
            capabilities=[],
            factory=lambda cfg: _FakeGraphTool(cfg),
            requires_auth=False,
            default_enabled=True,
        )
    )
    return registry


def _capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry(tool_registry=_tool_registry())
    registry.register(
        CapabilityVersion(
            capability_id="query_knowledge_graph",
            version=1,
            description="d",
            input_schema={"query": "str", "parameters": "dict"},
            output_schema={"data": "dict", "summary": "str", "evidence_items": "list[str]"},
            scope_ceiling="the single Neo4j instance",
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
    return registry


def _seeded_policy_store() -> PolicyStore:
    store = PolicyStore()
    store.load(
        PolicyScopeLevel.SYSTEM,
        seed_system_policy_allowing(
            "query_knowledge_graph", authored_by="ops", effective_at="2026-08-17T00:00:00Z"
        ),
    )
    return store


def _control_plane(
    db_session: AsyncSession, *, policy_store: PolicyStore | None = None
) -> ControlPlane:
    return ControlPlane(
        capability_registry=_capability_registry(),
        tool_executor=ToolExecutor(registry=_tool_registry()),
        policy_store=policy_store if policy_store is not None else _seeded_policy_store(),
        event_repository=EngineeringEventRepository(db_session),
    )


async def _plan_step(
    db_session: AsyncSession, *, task_id: uuid.UUID, postcondition: str
) -> uuid.UUID:
    """Appends a real Goal -> Plan -> PlanStep chain and returns the
    PlanStepCreated event's own id — the only handle
    `request_verification` accepts."""
    repo = EngineeringEventRepository(db_session)
    goal = await repo.append(
        task_id=task_id,
        event_type=ev.GOAL_CREATED,
        payload={"description": "d", "postconditions": ["x"]},
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
            "description": "run the verification",
            "postcondition": postcondition,
        },
        actor="t",
        causation_event_id=plan.id,
    )
    return step.id


def _generator_action(**overrides: object) -> Action:
    defaults: dict[str, object] = {
        "action_id": uuid.uuid4(),
        "capability_id": "query_knowledge_graph",
        "capability_version": 1,
        "parameters": {"query": "find repos", "parameters": {}},
        "prediction": Prediction(
            target_observable="summary",
            falsification_condition="summary is empty",
            evaluation_procedure="check keys",
            execution_context={},
            necessary_condition_rationale="needed for the plan step",
        ),
        "plan_step_id": uuid.uuid4(),
    }
    defaults.update(overrides)
    return Action(**defaults)  # type: ignore[arg-type]


class TestHappyPath:
    async def test_verification_dispatches_and_records_a_classified_observation(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        plan_step_event_id = await _plan_step(
            db_session, task_id=task_id, postcondition="the repository count is reported"
        )

        result = await control_plane.request_verification(
            task_id=task_id, plan_step_event_id=plan_step_event_id
        )

        assert result.outcome == "completed"
        assert result.tool_success is True

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        observation = events[-1]
        assert observation.event_type == ev.OBSERVATION_RECORDED
        assert observation.payload["actor"] == _VERIFIER_ACTOR
        assert observation.payload["plan_step_id"] == str(plan_step_event_id)
        assert observation.payload["outcome"] == "completed"
        assert observation.payload["classification"] == "expected"

    async def test_unknown_plan_step_event_id_is_rejected(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        await _plan_step(db_session, task_id=task_id, postcondition="x")

        with pytest.raises(VerificationTargetNotFoundError):
            await control_plane.request_verification(
                task_id=task_id, plan_step_event_id=uuid.uuid4()
            )


class TestVerifierIndependence:
    async def test_verifier_actor_is_fixed_regardless_of_generator_activity(
        self, db_session: AsyncSession
    ) -> None:
        """The generator's OWN Observation (an ordinary
        `authorize_and_execute` call, the public API, carrying no actor
        override at all — none exists) must never influence the
        SEPARATE Observation `request_verification` later produces."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        plan_step_event_id = await _plan_step(db_session, task_id=task_id, postcondition="x")

        generator_action = _generator_action(plan_step_id=plan_step_event_id)
        await control_plane.authorize_and_execute(
            task_id=task_id,
            action=generator_action,
            human_approval=None,
        )

        result = await control_plane.request_verification(
            task_id=task_id, plan_step_event_id=plan_step_event_id
        )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        generator_observation = next(
            e
            for e in events
            if e.event_type == ev.OBSERVATION_RECORDED
            and e.payload.get("action_id") == str(generator_action.action_id)
        )
        verifier_observation = next(
            e
            for e in events
            if e.event_type == ev.OBSERVATION_RECORDED
            and e.payload.get("action_id") != str(generator_action.action_id)
        )
        # The generator's own Observation never carries the reserved
        # verifier identity — the public API has no way to set it.
        assert generator_observation.payload["actor"] is None
        assert verifier_observation.payload["actor"] == _VERIFIER_ACTOR
        assert result.action_id != generator_action.action_id

    async def test_request_verification_accepts_no_verifier_selection_argument(self) -> None:
        """Structural proof, not merely runtime behavior — mirrors
        `tests/unit/architecture/test_verification_boundary.py`'s
        signature check from the integration side: calling with a
        `verifier`-shaped keyword argument raises `TypeError` at the
        Python level, before any authorization logic runs at all."""
        control_plane_cls = ControlPlane
        with pytest.raises(TypeError):
            await control_plane_cls.request_verification(  # type: ignore[call-arg]
                object(),  # not a real ControlPlane — never reached
                task_id=uuid.uuid4(),
                plan_step_event_id=uuid.uuid4(),
                verifier_actor="attacker_supplied",  # type: ignore[call-arg]
            )

    async def test_authorize_and_execute_public_api_accepts_no_actor_override(
        self, db_session: AsyncSession
    ) -> None:
        """Phase 5 exit-audit correction, requirement 5 — the exact
        adversarial reproduction that originally exposed the defect: a
        direct call to the PUBLIC `authorize_and_execute` (bypassing
        `request_verification`/`VerificationService` entirely) can no
        longer forge `actor == "control_plane_verifier"`, because the
        parameter that once accepted it no longer exists on this
        method's signature at all — this is now a `TypeError`, not a
        successful forgery."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action = _generator_action()

        with pytest.raises(TypeError):
            await control_plane.authorize_and_execute(  # type: ignore[call-arg]
                task_id=task_id,
                action=action,
                human_approval=None,
                business_actor=_VERIFIER_ACTOR,  # type: ignore[call-arg]
            )

        # And the (unrelated) generic path still works normally, with no
        # actor override possible — proving this isn't a blanket breakage.
        result = await control_plane.authorize_and_execute(
            task_id=task_id, action=action, human_approval=None
        )
        assert result.tool_success is True
        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        observation = next(e for e in events if e.event_type == ev.OBSERVATION_RECORDED)
        assert observation.payload["actor"] is None
        assert observation.payload["actor"] != _VERIFIER_ACTOR

    async def test_verifier_gets_its_own_fresh_grant_independent_of_generator_grant_state(
        self, db_session: AsyncSession
    ) -> None:
        """Cap §15: the verifier's authorization must never be a reuse
        of, or dependent on, the generator's own Grant/Authorization
        state. Proven by never even issuing a generator Grant for this
        PlanStep at all — Verification still succeeds and produces its
        own, independent AuthorizationGranted -> Consuming -> Consumed
        chain."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        plan_step_event_id = await _plan_step(db_session, task_id=task_id, postcondition="x")

        result = await control_plane.request_verification(
            task_id=task_id, plan_step_event_id=plan_step_event_id
        )
        assert result.tool_success is True

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        event_types = [e.event_type for e in events]
        assert ev.AUTHORIZATION_GRANTED in event_types
        assert ev.AUTHORIZATION_CONSUMED in event_types

    async def test_a_genuine_policy_denial_of_the_capability_also_denies_verification(
        self, db_session: AsyncSession
    ) -> None:
        """Corrected per the Phase 5 exit-audit test-quality finding:
        the previous version of this test denied via
        `capability_id="does_not_exist"` — a Conformance/CAPABILITY_GAP
        denial, never reaching Policy evaluation at all — which proved
        nothing about Policy's actor-blindness. This version denies
        `query_knowledge_graph` itself at the TASK Policy scope (a
        genuine `POLICY_DENIAL`), then shows Verification — invoking the
        SAME Capability, through the SAME `PolicyStore` — is ALSO
        denied, because Policy has no actor-scoping dimension to exempt
        the verifier through (see `app.control_plane.policy`, unchanged
        by Phase 5). This is the actually-honest statement of "Policy is
        actor-blind": it applies identically to generator and verifier,
        never selectively lenient to either."""
        store = _seeded_policy_store()
        store.load(
            PolicyScopeLevel.TASK,
            PolicyVersion(
                rules=(
                    PolicyRule(
                        capability_id="query_knowledge_graph",
                        effect=PolicyRuleEffect.DENY,
                        scope_level=PolicyScopeLevel.TASK,
                        reason="task under incident review",
                    ),
                ),
                authoring_authority="incident-response",
                effective_at="2026-08-17T00:00:00Z",
                supersedes=None,
            ),
        )
        control_plane = _control_plane(db_session, policy_store=store)
        task_id = uuid.uuid4()
        plan_step_event_id = await _plan_step(db_session, task_id=task_id, postcondition="x")

        generator_action = _generator_action(plan_step_id=plan_step_event_id)
        from app.control_plane.control_plane import AuthorizationDeniedError

        with pytest.raises(AuthorizationDeniedError):
            await control_plane.authorize_and_execute(
                task_id=task_id, action=generator_action, human_approval=None
            )

        with pytest.raises(AuthorizationDeniedError):
            await control_plane.request_verification(
                task_id=task_id, plan_step_event_id=plan_step_event_id
            )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        denials = [e for e in events if e.event_type == ev.AUTHORIZATION_DENIED]
        assert len(denials) == 2
        assert all(d.payload["denial_stage"] == "policy_denial" for d in denials)


class TestPlanStepBinding:
    async def test_verification_resolves_postcondition_from_the_durable_event_only(
        self, db_session: AsyncSession
    ) -> None:
        """`request_verification` accepts no postcondition parameter at
        all — proven by inspecting what Verification actually queried
        with: the pinned postcondition text, never anything a caller
        could have substituted (there is no parameter to substitute it
        through)."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        pinned = "the pinned postcondition text"
        plan_step_event_id = await _plan_step(db_session, task_id=task_id, postcondition=pinned)

        await control_plane.request_verification(
            task_id=task_id, plan_step_event_id=plan_step_event_id
        )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        observation = events[-1]
        assert observation.payload["raw_result"]["summary"] == f"result for: {pinned}"

    async def test_a_second_plan_step_with_a_different_postcondition_is_evaluated_independently(
        self, db_session: AsyncSession
    ) -> None:
        """Two PlanSteps, two different pinned postconditions, in the
        SAME task — proves Verification always resolves the postcondition
        belonging to the SPECIFIC `plan_step_event_id` passed, never a
        different one that happens to exist in the same task's history
        (the substitution attack this design closes structurally)."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        step_a = await _plan_step(db_session, task_id=task_id, postcondition="condition A")

        repo = EngineeringEventRepository(db_session)
        # A second Plan/PlanStep in the same task, a different postcondition.
        goal_b = await repo.append(
            task_id=task_id,
            event_type=ev.GOAL_CREATED,
            payload={"description": "d2", "postconditions": ["y"]},
            actor="t",
        )
        plan_b = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_CREATED,
            payload={"goal_event_id": str(goal_b.id), "scope": ["repo-b"]},
            actor="t",
            causation_event_id=goal_b.id,
        )
        step_b_event = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_CREATED,
            payload={
                "plan_event_id": str(plan_b.id),
                "description": "step b",
                "postcondition": "condition B",
            },
            actor="t",
            causation_event_id=plan_b.id,
        )

        await control_plane.request_verification(task_id=task_id, plan_step_event_id=step_a)
        await control_plane.request_verification(
            task_id=task_id, plan_step_event_id=step_b_event.id
        )

        events = await repo.list_for_task(task_id)
        observations = [e for e in events if e.event_type == ev.OBSERVATION_RECORDED]
        assert len(observations) == 2
        assert observations[0].payload["raw_result"]["summary"] == "result for: condition A"
        assert observations[1].payload["raw_result"]["summary"] == "result for: condition B"


class TestDurability:
    async def test_verification_result_survives_a_fresh_session_read(self) -> None:
        from app.database.session import AsyncSessionLocal
        from app.engineering_state.materialize import fold

        task_id = uuid.uuid4()
        async with AsyncSessionLocal() as session:
            plan_step_event_id = await _plan_step(session, task_id=task_id, postcondition="x")
            control_plane = _control_plane(session)
            await control_plane.request_verification(
                task_id=task_id, plan_step_event_id=plan_step_event_id
            )
            await session.commit()

        async with AsyncSessionLocal() as verify_session:
            repo = EngineeringEventRepository(verify_session)
            events = await repo.list_for_task(task_id)
            state = fold(events)
            assert len(state.observations) == 1
            assert state.observations[0].actor == _VERIFIER_ACTOR
            assert state.observations[0].classification == "expected"


class TestImmutability:
    async def test_committed_plan_step_postcondition_cannot_be_mutated(
        self, db_session: AsyncSession
    ) -> None:
        from sqlalchemy import text

        task_id = uuid.uuid4()
        plan_step_event_id = await _plan_step(
            db_session, task_id=task_id, postcondition="original postcondition"
        )
        await db_session.flush()

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text(
                    "UPDATE engineering_events SET payload = "
                    "jsonb_set(payload, '{postcondition}', '\"tampered\"') WHERE id = :id"
                ),
                {"id": str(plan_step_event_id)},
            )

    async def test_committed_observation_classification_cannot_be_mutated(
        self, db_session: AsyncSession
    ) -> None:
        from sqlalchemy import text

        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        plan_step_event_id = await _plan_step(db_session, task_id=task_id, postcondition="x")
        await control_plane.request_verification(
            task_id=task_id, plan_step_event_id=plan_step_event_id
        )
        await db_session.flush()

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        observation_id = events[-1].id

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text(
                    "UPDATE engineering_events SET payload = "
                    "jsonb_set(payload, '{classification}', '\"expected\"') WHERE id = :id"
                ),
                {"id": str(observation_id)},
            )


class TestActionOutcomeUnknownBlocksDependents:
    async def test_unresolved_outcome_unknown_blocks_eligibility(
        self, db_session: AsyncSession
    ) -> None:
        """No producer in this codebase can yet durably create an
        `outcome_unknown` Observation through real dispatch (see
        `ControlPlane._consume_and_dispatch`'s own docstring — Tool
        dispatch is always synchronous and determinate today); this
        appends one directly, simulating the future producer, to prove
        the DERIVATION and BLOCKING logic itself, independent of when a
        real indeterminate-dispatch producer eventually exists."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        repo = EngineeringEventRepository(db_session)
        await repo.append(
            task_id=task_id,
            event_type=ev.OBSERVATION_RECORDED,
            payload={
                "raw_result": {"note": "dispatch timed out, remote effect unknown"},
                "capability": "query_knowledge_graph",
                "outcome": "outcome_unknown",
            },
            actor="control_plane",
        )

        events = await repo.list_for_task(task_id)
        state = fold(events)
        assert has_unresolved_outcome_unknown(state) is True

        dependent_action = _generator_action()
        eligibility = control_plane.check_eligibility(
            dependent_action,
            budget_available=True,
            lease_held=True,
            prior_action_halted=False,
            preconditions_hold=not has_unresolved_outcome_unknown(state),
        )
        assert eligibility.eligible is False
        assert eligibility.denial_stage == DenialStage.PRECONDITION_INVALIDATED

    async def test_task_with_no_outcome_unknown_observation_is_not_blocked(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        plan_step_event_id = await _plan_step(db_session, task_id=task_id, postcondition="x")
        await control_plane.request_verification(
            task_id=task_id, plan_step_event_id=plan_step_event_id
        )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        state = fold(events)
        assert has_unresolved_outcome_unknown(state) is False

        dependent_action = _generator_action()
        eligibility = control_plane.check_eligibility(
            dependent_action,
            budget_available=True,
            lease_held=True,
            prior_action_halted=False,
            preconditions_hold=not has_unresolved_outcome_unknown(state),
        )
        assert eligibility.eligible is True
