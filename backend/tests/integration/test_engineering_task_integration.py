"""Real-Postgres proof of Phase 7's minimal end-to-end integration: a
real, authenticated HTTP request traverses the entire Phase 1-6 stack —
`GoalCreated` -> `ReasoningPlane` -> `PlanCreated`/`PlanStepCreated` ->
`ActionProposal` -> `ControlPlane` authorization -> `query_knowledge_graph`
-> `ObservationRecorded` -> Independent Verification -> verifier
`ObservationRecorded`.

Uses a locally-built `CapabilityRegistry`/`PolicyStore`/`ToolRegistry`
bound to a fake `neo4j_graph` Tool, injected via
`app.dependency_overrides` — mirroring this codebase's own established
`db_client`/`get_db_session` override convention, and matching every
prior phase's own precedent of testing `query_knowledge_graph` against a
fake Tool (no real Neo4j is part of this test suite's infrastructure —
confirmed by grep against every existing test that exercises this
Capability).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
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
from app.control_plane.policy import (
    PolicyRule,
    PolicyRuleEffect,
    PolicyScopeLevel,
    PolicyStore,
    PolicyVersion,
    seed_system_policy_allowing,
)
from app.control_plane.runtime import get_capability_registry, get_policy_store
from app.database.session import get_db_session
from app.engineering_state import events as ev
from app.engineering_state.materialize import fold, is_plan_step_invalidated
from app.main import create_app
from app.models.engineering_event import EngineeringEvent
from app.repositories.engineering_event_repository import EngineeringEventRepository
from app.tools.interfaces import ToolCategory, ToolHealth, ToolInput, ToolResult
from app.tools.registry import ToolRegistry, ToolSpec, get_tool_registry

pytestmark = pytest.mark.asyncio

_REGISTER_PAYLOAD = {
    "email": "engineering-task-tester@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Engineering Task Tester",
}


class _FakeGraphTool:
    """Mirrors `tests/integration/test_verification_integration.py`'s
    own `_FakeGraphTool` shape exactly — a canned, deterministic
    response, no real Neo4j."""

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
            data={"data": {}, "summary": f"result for: {input.query}"},
        )

    async def health_check(self) -> ToolHealth:
        return ToolHealth.HEALTHY

    def requires_auth(self) -> bool:
        return False


class _FailingGraphTool(_FakeGraphTool):
    async def execute(self, input: ToolInput) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=False,
            error="simulated infrastructure failure",
        )


class _ContradictingGraphTool(_FakeGraphTool):
    """Returns an empty summary — falsifies the Prediction
    `ReasoningPlane` always constructs (`target_observable="summary"`,
    falsified by emptiness), producing `classification="contradiction"`."""

    async def execute(self, input: ToolInput) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={"data": {}, "summary": ""},
        )


def _tool_registry(tool_cls: type = _FakeGraphTool) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            tool_id="neo4j_graph",
            display_name="Fake Graph",
            description="d",
            category=ToolCategory.GRAPH,
            capabilities=[],
            factory=lambda cfg: tool_cls(cfg),
            requires_auth=False,
            default_enabled=True,
        )
    )
    return registry


def _capability_registry(tool_cls: type = _FakeGraphTool) -> CapabilityRegistry:
    registry = CapabilityRegistry(tool_registry=_tool_registry(tool_cls))
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
            "query_knowledge_graph", authored_by="test", effective_at="2026-08-18T00:00:00Z"
        ),
    )
    return store


def _denying_policy_store() -> PolicyStore:
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
            effective_at="2026-08-18T00:00:00Z",
            supersedes=None,
        ),
    )
    return store


def _override_app(
    db_session: AsyncSession,
    *,
    tool_cls: type = _FakeGraphTool,
    policy_store: PolicyStore | None = None,
):
    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_capability_registry] = lambda: _capability_registry(tool_cls)
    app.dependency_overrides[get_policy_store] = lambda: (policy_store or _seeded_policy_store())
    app.dependency_overrides[get_tool_registry] = lambda: _tool_registry(tool_cls)
    return app


@pytest.fixture
async def engineering_task_client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """Like `db_client`, but additionally overrides
    `get_capability_registry`/`get_policy_store`/`get_tool_registry` with
    a locally-built, fake-Tool-backed set — see this module's own
    docstring for why."""
    app = _override_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _register_and_login(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    await client.post("/api/v1/auth/register", json=_REGISTER_PAYLOAD)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": _REGISTER_PAYLOAD["email"], "password": _REGISTER_PAYLOAD["password"]},
    )
    token = login.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}"}, uuid.UUID(me.json()["id"])


class TestHappyPath:
    async def test_real_request_traverses_the_full_stack(
        self, engineering_task_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, _ = await _register_and_login(engineering_task_client)

        response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers,
            json={
                "description": "find repositories containing payment processing code",
                "postconditions": ["at least one repository identified"],
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["generator_observation"]["classification"] == "expected"
        assert body["generator_observation"]["success"] is True
        assert body["verifier_observation"]["classification"] == "expected"
        assert body["verifier_observation"]["actor"] == "control_plane_verifier"

        task_id = uuid.UUID(body["task_id"])
        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        event_types = [e.event_type for e in events]

        assert event_types == [
            ev.GOAL_CREATED,
            ev.PLAN_CREATED,
            ev.PLAN_STEP_CREATED,
            ev.AUTHORIZATION_GRANTED,
            ev.AUTHORIZATION_CONSUMING,
            ev.AUTHORIZATION_CONSUMED,
            ev.OBSERVATION_RECORDED,
            ev.AUTHORIZATION_GRANTED,
            ev.AUTHORIZATION_CONSUMING,
            ev.AUTHORIZATION_CONSUMED,
            ev.OBSERVATION_RECORDED,
        ]
        # No DecisionMade (read-only Action, ES §12 exclusion) and no
        # ActionProposed (never invented — see the Phase 7 design).
        assert ev.DECISION_MADE not in event_types

        generator_observation = events[6]
        assert generator_observation.payload["actor"] is None
        verifier_observation = events[10]
        assert verifier_observation.payload["actor"] == "control_plane_verifier"
        assert verifier_observation.payload["plan_step_id"] == str(events[2].id)


class TestPolicyDenial:
    async def test_task_scoped_policy_denial_prevents_tool_execution(
        self, db_session: AsyncSession
    ) -> None:
        app = _override_app(db_session, policy_store=_denying_policy_store())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers, _ = await _register_and_login(client)
            response = await client.post(
                "/api/v1/engineering-tasks",
                headers=headers,
                json={"description": "find repositories", "postconditions": ["found"]},
            )
        app.dependency_overrides.clear()

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "engineering_task_denied"

        # The denial response carries no task_id (the request never
        # completed successfully) — recovered instead by scanning for the
        # most recent GoalCreated event this test created (the API
        # boundary appends+flushes it before the pipeline ever reaches
        # Policy; `db_session` sees it read-your-own-writes, uncommitted).
        # Ordered by recorded_at, not a bare `scalar_one()`: other tests
        # in this module that genuinely commit via real `AsyncSessionLocal()`
        # sessions (see TestReplay) leave their own GoalCreated rows
        # permanently in the shared dev database across separate runs —
        # an unscoped "the one Goal" lookup is not safe against that.
        result = await db_session.execute(
            select(EngineeringEvent.task_id)
            .where(EngineeringEvent.event_type == ev.GOAL_CREATED)
            .order_by(EngineeringEvent.recorded_at.desc())
            .limit(1)
        )
        task_id = result.scalar_one()

        # Conformance succeeded (the request reached final-gate Policy
        # evaluation, not an earlier structural rejection) — proven by
        # the presence of Plan/PlanStep events before the denial.
        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        event_types = [e.event_type for e in events]
        assert ev.PLAN_STEP_CREATED in event_types
        assert ev.AUTHORIZATION_DENIED in event_types
        denial = next(e for e in events if e.event_type == ev.AUTHORIZATION_DENIED)
        assert denial.payload["denial_stage"] == "policy_denial"
        # The Tool was never dispatched — no Observation exists at all.
        assert ev.OBSERVATION_RECORDED not in event_types
        assert ev.AUTHORIZATION_GRANTED not in event_types


class TestVerifierIdentity:
    async def test_verifier_actor_is_fixed_and_distinct_from_generator(
        self, engineering_task_client: AsyncClient
    ) -> None:
        headers, _ = await _register_and_login(engineering_task_client)
        response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers,
            json={"description": "find repositories", "postconditions": ["found"]},
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["verifier_observation"]["actor"] == "control_plane_verifier"
        assert body["generator_observation"]["actor"] != "control_plane_verifier"

        # ReasoningPlane/EngineeringTaskService cannot influence this —
        # neither imports `_VERIFIER_ACTOR` nor passes any actor value to
        # `request_verification` (structurally proven in
        # tests/unit/architecture/test_reasoning_plane_boundary.py).


class TestReplay:
    async def test_fresh_session_replay_reconstructs_the_complete_lifecycle(self) -> None:
        """Calls `EngineeringTaskService` directly with a real, separately
        COMMITTING `AsyncSessionLocal()` session — not through
        `db_client`/`engineering_task_client`, whose underlying
        `db_session` fixture wraps everything in a savepoint that is
        always rolled back at teardown and therefore never durably
        visible to a genuinely independent connection (see
        `tests/integration/test_control_plane_authorization_integration.py
        ::TestCrashWindowDurability`'s identical rationale for using real
        sessions instead of the shared fixture for exactly this reason).
        """
        from app.database.session import AsyncSessionLocal
        from app.services.engineering_task_service import EngineeringTaskService

        task_id_holder: dict[str, uuid.UUID] = {}
        async with AsyncSessionLocal() as session:
            service = EngineeringTaskService(
                db=session,
                capability_registry=_capability_registry(),
                policy_store=_seeded_policy_store(),
                tool_registry=_tool_registry(),
            )
            result = await service.create_and_execute(
                description="find repositories",
                postconditions=["found"],
                user_id=uuid.uuid4(),
            )
            task_id_holder["task_id"] = result.task_id
            # create_and_execute already commits internally on success.

        async with AsyncSessionLocal() as verify_session:
            repo = EngineeringEventRepository(verify_session)
            events = await repo.list_for_task(task_id_holder["task_id"])
            state = fold(events)

            assert state.goal is not None
            assert len(state.plans) == 1
            assert len(state.plan_steps) == 1
            assert len(state.observations) == 2
            assert state.observations[0].classification == "expected"
            assert state.observations[1].classification == "expected"
            assert len(state.authorization_grants) == 2
            assert all(g.state == "consumed" for g in state.authorization_grants)


class TestToolFailure:
    async def test_tool_failure_classifies_as_anomaly_not_expected(
        self, db_session: AsyncSession
    ) -> None:
        app = _override_app(db_session, tool_cls=_FailingGraphTool)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers, _ = await _register_and_login(client)
            response = await client.post(
                "/api/v1/engineering-tasks",
                headers=headers,
                json={"description": "find repositories", "postconditions": ["found"]},
            )
        app.dependency_overrides.clear()

        # The generator's own dispatch fails; Verification's SEPARATE
        # dispatch (a fresh Tool invocation) also fails identically for
        # the same fake Tool — both surface as anomaly, never "expected".
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["generator_observation"]["success"] is False
        assert body["generator_observation"]["classification"] == "anomaly"
        assert body["verifier_observation"]["classification"] == "anomaly"


class TestContradictionReadiness:
    async def test_contradiction_observation_can_be_durably_represented_and_consumed(
        self, db_session: AsyncSession
    ) -> None:
        """Does NOT implement the Replan loop — only proves that a
        Contradiction Observation this integration can genuinely produce
        is representable via Phase 6's existing
        `PlanStepInvalidated`/materialization primitives, unmodified."""
        app = _override_app(db_session, tool_cls=_ContradictingGraphTool)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers, _ = await _register_and_login(client)
            response = await client.post(
                "/api/v1/engineering-tasks",
                headers=headers,
                json={"description": "find repositories", "postconditions": ["found"]},
            )
        app.dependency_overrides.clear()

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["generator_observation"]["classification"] == "contradiction"

        task_id = uuid.UUID(body["task_id"])
        plan_step_event_id = uuid.UUID(body["plan_step_event_id"])
        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        contradiction_observation = next(
            e
            for e in events
            if e.event_type == ev.OBSERVATION_RECORDED
            and e.payload.get("classification") == "contradiction"
        )

        # A future Reasoning cycle's job — proven reachable with existing,
        # unmodified Phase 6 primitives, not built into a live loop here.
        await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_INVALIDATED,
            payload={
                "plan_step_event_id": str(plan_step_event_id),
                "contradiction_observation_event_id": str(contradiction_observation.id),
                "reason": "postcondition falsified by the generator's own Observation",
            },
            actor="test",
            causation_event_id=plan_step_event_id,
        )

        state = fold(await repo.list_for_task(task_id))
        assert is_plan_step_invalidated(state, plan_step_event_id) is True
