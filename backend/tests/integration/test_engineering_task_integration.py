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


class _CredentialLeakingGraphTool(_FakeGraphTool):
    """Phase 8 — simulates a Tool whose failure text happens to contain
    credential-shaped substrings (the exact residual risk the Phase 8
    Design Audit §4 names: a future Capability's `except Exception:
    error=str(exc)` catch-all is not guaranteed credential-free). Used
    to prove `redact_secrets()` is actually applied end-to-end, through
    a real HTTP round trip, not just at the unit level."""

    async def execute(self, input: ToolInput) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=False,
            error=(
                "connection failed: aws_access_key_id=AKIAABCDEFGHIJKLMNOP "
                "api_key: 'sk-live-abcdef1234567890' "
                "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ"
            ),
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


class _CapturingGraphTool(_FakeGraphTool):
    """Phase 9 — records every `ToolInput` it actually receives, so a
    test can inspect exactly what `ControlPlane._resolve_runtime_parameter`
    injected, end-to-end through the real HTTP/API path — not merely at
    the unit level. A class-level list (the factory constructs a fresh
    instance per dispatch, matching every other fake tool's own
    convention) — tests using this fixture MUST clear it first."""

    received_inputs: list[ToolInput] = []

    async def execute(self, input: ToolInput) -> ToolResult:
        type(self).received_inputs.append(input)
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={"data": {}, "summary": f"result for: {input.query}"},
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
            # Phase 9 — matches the real registration in
            # app.capabilities.setup exactly, so this test file's fixture
            # exercises the same runtime-injection path production does.
            runtime_injected_parameters=frozenset({"db", "user_id"}),
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
    app.dependency_overrides[get_policy_store] = lambda: policy_store or _seeded_policy_store()
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
    return await _register_and_login_as(client, _REGISTER_PAYLOAD["email"])


async def _register_and_login_as(
    client: AsyncClient,
    email: str,
    *,
    is_active: bool = True,
    db_session: AsyncSession | None = None,
) -> tuple[dict[str, str], uuid.UUID]:
    """Phase 7.3 (ownership fix) — the multi-user variant `TestOwnership`
    needs: `_register_and_login` alone always uses the same fixed
    identity, which cannot prove cross-user isolation.

    `is_active=False` (requires `db_session` — the SAME transactional
    session `engineering_task_client` is wired to, per
    `_override_app`/`override_get_db_session` above) is set directly here
    purely to exercise `get_current_user`'s own, pre-existing, unmodified
    `is_active` check — not a new mechanism this phase introduces. A
    genuinely separate `AsyncSessionLocal()` would not see this test's
    still-uncommitted `register` write (`db_session` wraps the whole test
    in one transaction, rolled back at teardown, per its own docstring
    above) — this must reuse the identical session the HTTP client itself
    reads and writes through.
    """
    password = "correct-horse-battery-staple"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": email},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    user_id = uuid.UUID(me.json()["id"])

    if not is_active:
        assert db_session is not None, "is_active=False requires db_session"
        from sqlalchemy import update as sa_update

        from app.models.user import User

        await db_session.execute(sa_update(User).where(User.id == user_id).values(is_active=False))
        await db_session.flush()

    return {"Authorization": f"Bearer {token}"}, user_id


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


class TestObservationDetailSurfacing:
    """Phase 8 — Observation/Evidence Detail Surfacing. Proves `summary`/
    `error`/`capability` are correctly projected end-to-end through a
    real HTTP round trip, and that `redact_secrets()` is genuinely
    applied before the response ever leaves the server — not merely
    exercised at the unit level."""

    async def test_expected_observation_exposes_summary_and_capability(
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

        for observation_key in ("generator_observation", "verifier_observation"):
            observation = body[observation_key]
            assert observation["capability"] == "query_knowledge_graph"
            assert observation["summary"] is not None
            assert "result for:" in observation["summary"]
            assert observation["error"] is None

    async def test_anomaly_observation_exposes_the_actual_tool_reported_error(
        self, db_session: AsyncSession
    ) -> None:
        """The core Phase 8 fix: an Anomaly is no longer a dead end — the
        real Tool-reported reason is now readable."""
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

        assert response.status_code == 201, response.text
        body = response.json()
        for observation_key in ("generator_observation", "verifier_observation"):
            observation = body[observation_key]
            assert observation["classification"] == "anomaly"
            assert observation["error"] == "simulated infrastructure failure"
            assert observation["capability"] == "query_knowledge_graph"

    async def test_credential_shaped_error_text_is_redacted_before_leaving_the_api(
        self, db_session: AsyncSession
    ) -> None:
        """Security requirement: known credential/token patterns are
        redacted before API exposure — proven by asserting the RAW
        secret values are absent from the response, not merely that
        SOME redaction marker is present."""
        app = _override_app(db_session, tool_cls=_CredentialLeakingGraphTool)
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
        raw_body_text = response.text

        # The raw secret substrings must not appear ANYWHERE in the
        # response body — not just in the field we expect them in.
        assert "AKIAABCDEFGHIJKLMNOP" not in raw_body_text
        assert "sk-live-abcdef1234567890" not in raw_body_text
        assert "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ" not in raw_body_text

        error_text = response.json()["generator_observation"]["error"]
        assert error_text is not None
        assert "[REDACTED:" in error_text

    async def test_historical_observation_with_no_summary_or_error_key_is_none(
        self, db_session: AsyncSession
    ) -> None:
        """A pre-Phase-8 Observation whose `raw_result` never had
        `summary`/`error` keys at all (only `success`) must not crash
        and must project as `None`, not an empty string or a KeyError."""
        from app.engineering_state import events as ev
        from app.engineering_state.materialize import fold
        from app.services.engineering_task_service import _observation_view

        repo = EngineeringEventRepository(db_session)
        task_id = uuid.uuid4()
        goal_event = await repo.append(
            task_id=task_id,
            event_type=ev.GOAL_CREATED,
            payload={"description": "d", "postconditions": ["p"]},
            actor="api:engineering_tasks",
        )
        plan_event = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_CREATED,
            payload={"goal_event_id": str(goal_event.id), "scope": []},
            actor="test",
            causation_event_id=goal_event.id,
        )
        plan_step_event = await repo.append(
            task_id=task_id,
            event_type=ev.PLAN_STEP_CREATED,
            payload={
                "plan_event_id": str(plan_event.id),
                "description": "d",
                "postcondition": "p",
            },
            actor="test",
            causation_event_id=plan_event.id,
        )
        await repo.append(
            task_id=task_id,
            event_type=ev.OBSERVATION_RECORDED,
            payload={
                # Pre-Phase-8 shape: only `success` inside raw_result,
                # no `summary`/`error` keys at all.
                "raw_result": {"success": True},
                "capability": "query_knowledge_graph",
                "action_id": str(uuid.uuid4()),
                "tool_id": "neo4j_graph",
                "success": True,
                "grant_id": str(uuid.uuid4()),
                "plan_step_id": str(plan_step_event.id),
            },
            actor="control_plane",
        )
        await db_session.commit()

        events = await repo.list_for_task(task_id)
        state = fold(events)
        observation = state.observations[0]
        view = _observation_view(observation)

        assert view.summary is None
        assert view.error is None
        assert view.capability == "query_knowledge_graph"
        assert view.success is True


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


class TestGetEndpoint:
    """Phase 7.1 — the read-only visibility slice."""

    async def test_post_then_get_round_trip(
        self, engineering_task_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, _ = await _register_and_login(engineering_task_client)
        create_response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers,
            json={
                "description": "find repositories containing payment processing code",
                "postconditions": ["at least one repository identified"],
            },
        )
        assert create_response.status_code == 201, create_response.text
        created = create_response.json()
        task_id = created["task_id"]

        get_response = await engineering_task_client.get(
            f"/api/v1/engineering-tasks/{task_id}", headers=headers
        )
        assert get_response.status_code == 200, get_response.text
        fetched = get_response.json()

        # 1. Existing task returns 200 — proven by the assertion above.
        # 2. Goal is reconstructed correctly.
        assert fetched["goal"]["description"] == (
            "find repositories containing payment processing code"
        )
        assert fetched["goal"]["postconditions"] == ["at least one repository identified"]
        # 3. Plan is reconstructed correctly.
        assert fetched["plan_event_id"] == created["plan_event_id"]
        # 4. PlanStep is reconstructed correctly.
        assert fetched["plan_step"]["event_id"] == created["plan_step_event_id"]
        assert fetched["plan_step"]["description"] == (
            "find repositories containing payment processing code"
        )
        assert fetched["plan_step"]["postcondition"]
        assert fetched["plan_step"]["invalidated"] is False
        # 5. Observation is reconstructed correctly.
        assert fetched["generator_observation"]["success"] is True
        # 6. Classification is returned correctly.
        assert fetched["generator_observation"]["classification"] == "expected"
        # 7. Verification result is returned correctly.
        assert fetched["verifier_observation"]["classification"] == "expected"
        assert fetched["verifier_observation"]["actor"] == "control_plane_verifier"
        assert fetched["task_id"] == task_id
        assert fetched["created_at"]

        # The GET request itself appended nothing — same event count
        # before and after, read via the shared db_session.
        repo = EngineeringEventRepository(db_session)
        events_after_get = await repo.list_for_task(uuid.UUID(task_id))
        assert len(events_after_get) == 11  # the exact Phase 7 happy-path count

    async def test_nonexistent_task_returns_404(self, engineering_task_client: AsyncClient) -> None:
        headers, _ = await _register_and_login(engineering_task_client)
        response = await engineering_task_client.get(
            f"/api/v1/engineering-tasks/{uuid.uuid4()}", headers=headers
        )
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"

    async def test_get_performs_no_mutation(
        self, engineering_task_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, _ = await _register_and_login(engineering_task_client)
        create_response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers,
            json={"description": "find repositories", "postconditions": ["found"]},
        )
        task_id = create_response.json()["task_id"]

        repo = EngineeringEventRepository(db_session)
        events_before = await repo.list_for_task(uuid.UUID(task_id))
        event_ids_before = {e.id for e in events_before}

        # Two GETs in a row — neither should change anything.
        await engineering_task_client.get(f"/api/v1/engineering-tasks/{task_id}", headers=headers)
        await engineering_task_client.get(f"/api/v1/engineering-tasks/{task_id}", headers=headers)

        events_after = await repo.list_for_task(uuid.UUID(task_id))
        event_ids_after = {e.id for e in events_after}
        assert event_ids_after == event_ids_before
        assert len(events_after) == len(events_before)

    async def test_fresh_session_get_reconstructs_the_same_state(self) -> None:
        """State can be reconstructed from a fresh DB session — the GET
        path is exercised against a genuinely independent connection,
        not one that shares any in-memory object with how the task was
        created."""
        from app.database.session import AsyncSessionLocal
        from app.services.engineering_task_service import (
            EngineeringTaskService,
            get_engineering_task,
        )

        creating_user_id = uuid.uuid4()
        async with AsyncSessionLocal() as create_session:
            service = EngineeringTaskService(
                db=create_session,
                capability_registry=_capability_registry(),
                policy_store=_seeded_policy_store(),
                tool_registry=_tool_registry(),
            )
            created = await service.create_and_execute(
                description="find repositories",
                postconditions=["found"],
                user_id=creating_user_id,
            )

        async with AsyncSessionLocal() as fresh_session:
            fetched = await get_engineering_task(
                db=fresh_session, task_id=created.task_id, requesting_user_id=creating_user_id
            )

        assert fetched is not None
        assert fetched.task_id == created.task_id
        assert fetched.goal.description == "find repositories"
        assert fetched.plan_step is not None
        assert fetched.plan_step.postcondition == created.plan_step.postcondition
        assert fetched.generator_observation.classification == "expected"
        assert fetched.verifier_observation.classification == "expected"

    async def test_get_does_not_touch_legacy_workflow_run_or_agent_run_tables(
        self, engineering_task_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Behavioral companion to the structural architecture-test proof
        (`test_reasoning_plane_boundary.py`): the legacy `Run`/`Workflow`/
        `AgentRun` row counts are IDENTICAL before and after a GET call —
        real evidence the read path neither creates nor reads-with-a-
        side-effect any legacy row, not just "no import found by AST"."""
        from sqlalchemy import func
        from sqlalchemy import select as sa_select

        from app.models.agent_step import AgentStep
        from app.models.run import Run
        from app.models.workflow import Workflow

        async def _counts() -> tuple[int, int, int]:
            run_count = await db_session.scalar(sa_select(func.count()).select_from(Run))
            workflow_count = await db_session.scalar(sa_select(func.count()).select_from(Workflow))
            agent_step_count = await db_session.scalar(
                sa_select(func.count()).select_from(AgentStep)
            )
            return run_count or 0, workflow_count or 0, agent_step_count or 0

        headers, _ = await _register_and_login(engineering_task_client)
        create_response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers,
            json={"description": "find repositories", "postconditions": ["found"]},
        )
        task_id = create_response.json()["task_id"]

        counts_before = await _counts()
        response = await engineering_task_client.get(
            f"/api/v1/engineering-tasks/{task_id}", headers=headers
        )
        assert response.status_code == 200, response.text
        counts_after = await _counts()

        assert counts_after == counts_before


class TestListEndpoint:
    """Phase 7.2 — the Engineering Task list view's read path.

    This test file's own established convention is real, committed
    writes (see the module docstring) — tasks created by earlier tests
    in the same session remain in the database. So these tests never
    assert an exact total count; they assert this test's own task is
    present with correct data, and that relative ordering/no-mutation/
    legacy-isolation hold — properties that stay true regardless of how
    many other tasks already exist.
    """

    async def test_list_includes_created_task_with_correct_summary_fields(
        self, engineering_task_client: AsyncClient
    ) -> None:
        headers, _ = await _register_and_login(engineering_task_client)
        create_response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers,
            json={
                "description": "find repositories containing payment processing code",
                "postconditions": ["at least one repository identified"],
            },
        )
        assert create_response.status_code == 201, create_response.text
        created = create_response.json()

        list_response = await engineering_task_client.get(
            "/api/v1/engineering-tasks", headers=headers
        )
        assert list_response.status_code == 200, list_response.text
        summaries = list_response.json()

        matching = [s for s in summaries if s["task_id"] == created["task_id"]]
        assert len(matching) == 1
        summary = matching[0]
        assert summary["description"] == ("find repositories containing payment processing code")
        assert summary["classification"] == "expected"
        assert summary["created_at"] == created["created_at"]
        assert summary["updated_at"]

    async def test_list_matches_detail_endpoint_classification(
        self, engineering_task_client: AsyncClient
    ) -> None:
        """The list and detail views must never disagree about one task —
        both are built from the same `_build_response` output."""
        headers, _ = await _register_and_login(engineering_task_client)
        create_response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers,
            json={"description": "find repositories", "postconditions": ["found"]},
        )
        task_id = create_response.json()["task_id"]

        detail_response = await engineering_task_client.get(
            f"/api/v1/engineering-tasks/{task_id}", headers=headers
        )
        list_response = await engineering_task_client.get(
            "/api/v1/engineering-tasks", headers=headers
        )
        detail = detail_response.json()
        summary = next(s for s in list_response.json() if s["task_id"] == task_id)

        assert summary["classification"] == detail["verifier_observation"]["classification"]
        assert summary["description"] == detail["goal"]["description"]
        assert summary["created_at"] == detail["created_at"]

    async def test_list_is_newest_first(self, engineering_task_client: AsyncClient) -> None:
        """Verifies the genuine, always-true invariant — `created_at` is
        non-increasing down the list — rather than assuming two
        sequential creates get strictly different timestamps.
        `recorded_at`'s column default is Postgres transaction-start
        time, not statement time; two requests issued back-to-back over
        an in-process ASGI transport (no real network hop) can
        legitimately tie. A tie is not an ordering violation — only a
        later entry with a STRICTLY GREATER `created_at` than an earlier
        one would be."""
        headers, _ = await _register_and_login(engineering_task_client)
        await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers,
            json={"description": "task A", "postconditions": ["found"]},
        )
        await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers,
            json={"description": "task B", "postconditions": ["found"]},
        )

        list_response = await engineering_task_client.get(
            "/api/v1/engineering-tasks", headers=headers
        )
        created_ats = [s["created_at"] for s in list_response.json()]

        assert created_ats == sorted(created_ats, reverse=True)

    async def test_list_performs_no_mutation(
        self, engineering_task_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, _ = await _register_and_login(engineering_task_client)
        create_response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers,
            json={"description": "find repositories", "postconditions": ["found"]},
        )
        task_id = create_response.json()["task_id"]

        repo = EngineeringEventRepository(db_session)
        events_before = await repo.list_for_task(uuid.UUID(task_id))
        event_ids_before = {e.id for e in events_before}

        await engineering_task_client.get("/api/v1/engineering-tasks", headers=headers)
        await engineering_task_client.get("/api/v1/engineering-tasks", headers=headers)

        events_after = await repo.list_for_task(uuid.UUID(task_id))
        event_ids_after = {e.id for e in events_after}
        assert event_ids_after == event_ids_before

    async def test_list_does_not_touch_legacy_workflow_run_or_agent_run_tables(
        self, engineering_task_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from sqlalchemy import func
        from sqlalchemy import select as sa_select

        from app.models.agent_step import AgentStep
        from app.models.run import Run
        from app.models.workflow import Workflow

        async def _counts() -> tuple[int, int, int]:
            run_count = await db_session.scalar(sa_select(func.count()).select_from(Run))
            workflow_count = await db_session.scalar(sa_select(func.count()).select_from(Workflow))
            agent_step_count = await db_session.scalar(
                sa_select(func.count()).select_from(AgentStep)
            )
            return run_count or 0, workflow_count or 0, agent_step_count or 0

        headers, _ = await _register_and_login(engineering_task_client)
        counts_before = await _counts()
        response = await engineering_task_client.get("/api/v1/engineering-tasks", headers=headers)
        assert response.status_code == 200, response.text
        counts_after = await _counts()

        assert counts_after == counts_before


class TestOwnership:
    """Phase 7.3 — the ownership fix. Reproduces the exact adversarial
    scenario from the Live Human UX Review's security finding, now
    proving it is closed, plus every adjacent edge case named in the
    Ownership Design Audit's §9 test plan."""

    async def test_owner_sees_their_own_task_in_list_and_detail(
        self, engineering_task_client: AsyncClient
    ) -> None:
        headers_a, _ = await _register_and_login_as(engineering_task_client, "owner-a@example.com")
        create_response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers_a,
            json={"description": "task A", "postconditions": ["found"]},
        )
        assert create_response.status_code == 201, create_response.text
        task_id = create_response.json()["task_id"]

        list_response = await engineering_task_client.get(
            "/api/v1/engineering-tasks", headers=headers_a
        )
        assert task_id in [s["task_id"] for s in list_response.json()]

        detail_response = await engineering_task_client.get(
            f"/api/v1/engineering-tasks/{task_id}", headers=headers_a
        )
        assert detail_response.status_code == 200, detail_response.text

    async def test_other_user_cannot_see_task_in_list_or_detail(
        self, engineering_task_client: AsyncClient
    ) -> None:
        """The exact scenario reproduced live against the running dev
        stack in the security review: User A creates a task, User B must
        never see it via either endpoint."""
        headers_a, _ = await _register_and_login_as(engineering_task_client, "owner-b1@example.com")
        headers_b, _ = await _register_and_login_as(
            engineering_task_client, "intruder-b1@example.com"
        )

        create_response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers_a,
            json={"description": "USER-A-SECRET-marker", "postconditions": ["found"]},
        )
        task_id = create_response.json()["task_id"]

        list_response = await engineering_task_client.get(
            "/api/v1/engineering-tasks", headers=headers_b
        )
        assert list_response.status_code == 200, list_response.text
        assert task_id not in [s["task_id"] for s in list_response.json()]
        assert not any("USER-A-SECRET-marker" in s["description"] for s in list_response.json())

        detail_response = await engineering_task_client.get(
            f"/api/v1/engineering-tasks/{task_id}", headers=headers_b
        )
        assert detail_response.status_code == 404, detail_response.text
        # Identical error shape to a genuinely nonexistent task — no
        # distinguishing signal.
        assert detail_response.json()["error"]["code"] == "not_found"

    async def test_404_error_matches_a_genuinely_nonexistent_task_exactly(
        self, engineering_task_client: AsyncClient
    ) -> None:
        headers_a, _ = await _register_and_login_as(engineering_task_client, "owner-b2@example.com")
        headers_b, _ = await _register_and_login_as(
            engineering_task_client, "intruder-b2@example.com"
        )
        create_response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers_a,
            json={"description": "task A", "postconditions": ["found"]},
        )
        task_id = create_response.json()["task_id"]

        someone_elses_task = await engineering_task_client.get(
            f"/api/v1/engineering-tasks/{task_id}", headers=headers_b
        )
        genuinely_nonexistent = await engineering_task_client.get(
            f"/api/v1/engineering-tasks/{uuid.uuid4()}", headers=headers_b
        )

        assert someone_elses_task.status_code == genuinely_nonexistent.status_code == 404
        assert someone_elses_task.json() == {
            "error": {
                "code": "not_found",
                "message": someone_elses_task.json()["error"]["message"],
            }
        }
        # Same error CODE for both — the only thing that legitimately
        # differs is the message's own echoed task_id text, not the shape
        # or the code.
        assert (
            someone_elses_task.json()["error"]["code"]
            == genuinely_nonexistent.json()["error"]["code"]
        )

    async def test_unauthenticated_request_is_rejected(
        self, engineering_task_client: AsyncClient
    ) -> None:
        list_response = await engineering_task_client.get("/api/v1/engineering-tasks")
        assert list_response.status_code == 401, list_response.text

        detail_response = await engineering_task_client.get(
            f"/api/v1/engineering-tasks/{uuid.uuid4()}"
        )
        assert detail_response.status_code == 401, detail_response.text

    async def test_deactivated_user_is_rejected(
        self, engineering_task_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Proves the EXISTING, unmodified `get_current_user.is_active`
        check already covers this — no new mechanism was needed."""
        headers, _ = await _register_and_login_as(
            engineering_task_client,
            "deactivated@example.com",
            is_active=False,
            db_session=db_session,
        )
        response = await engineering_task_client.get("/api/v1/engineering-tasks", headers=headers)
        assert response.status_code == 401, response.text

    async def test_historical_task_with_no_user_id_is_hidden_from_everyone(
        self, engineering_task_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Ownership Design Audit §4, Option C: a task whose `GoalCreated`
        predates this field must be invisible to EVERY user — never
        'visible to all' (the legacy Workflow behavior, explicitly
        rejected) — including the two real users below."""
        headers_a, _ = await _register_and_login_as(engineering_task_client, "owner-b3@example.com")
        headers_b, _ = await _register_and_login_as(
            engineering_task_client, "intruder-b3@example.com"
        )

        repo = EngineeringEventRepository(db_session)
        historical_task_id = uuid.uuid4()
        await repo.append(
            task_id=historical_task_id,
            event_type=ev.GOAL_CREATED,
            payload={"description": "pre-ownership-fix task", "postconditions": ["p"]},
            actor="api:engineering_tasks",
        )
        await db_session.commit()

        for headers in (headers_a, headers_b):
            list_response = await engineering_task_client.get(
                "/api/v1/engineering-tasks", headers=headers
            )
            assert str(historical_task_id) not in [s["task_id"] for s in list_response.json()]

            detail_response = await engineering_task_client.get(
                f"/api/v1/engineering-tasks/{historical_task_id}", headers=headers
            )
            assert detail_response.status_code == 404, detail_response.text

    async def test_admin_does_not_bypass_ownership(
        self, engineering_task_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Ownership Design Audit §5: no admin bypass — an admin is an
        ordinary caller for someone else's task, and for an unowned
        historical task, exactly like the legacy Workflow precedent."""
        from sqlalchemy import update as sa_update

        from app.models.user import User

        headers_owner, _ = await _register_and_login_as(
            engineering_task_client, "owner-b4@example.com"
        )
        headers_admin, admin_id = await _register_and_login_as(
            engineering_task_client, "admin-b4@example.com"
        )
        await db_session.execute(sa_update(User).where(User.id == admin_id).values(role="admin"))
        await db_session.commit()

        create_response = await engineering_task_client.post(
            "/api/v1/engineering-tasks",
            headers=headers_owner,
            json={"description": "owner's task", "postconditions": ["found"]},
        )
        task_id = create_response.json()["task_id"]

        list_response = await engineering_task_client.get(
            "/api/v1/engineering-tasks", headers=headers_admin
        )
        assert task_id not in [s["task_id"] for s in list_response.json()]

        detail_response = await engineering_task_client.get(
            f"/api/v1/engineering-tasks/{task_id}", headers=headers_admin
        )
        assert detail_response.status_code == 404, detail_response.text

    async def test_list_and_detail_never_disagree_about_who_may_see_a_task(
        self, engineering_task_client: AsyncClient
    ) -> None:
        """Structural consistency check, not just two separate
        assertions that could independently drift: for a set of tasks
        spanning both users, whatever the list includes must 200 on
        direct detail access by the SAME caller, and whatever it omits
        must 404 for that same caller."""
        headers_a, _ = await _register_and_login_as(engineering_task_client, "owner-b5@example.com")
        headers_b, _ = await _register_and_login_as(engineering_task_client, "other-b5@example.com")

        task_ids = []
        for i in range(3):
            resp = await engineering_task_client.post(
                "/api/v1/engineering-tasks",
                headers=headers_a,
                json={"description": f"task {i}", "postconditions": ["found"]},
            )
            task_ids.append(resp.json()["task_id"])

        for headers in (headers_a, headers_b):
            list_response = await engineering_task_client.get(
                "/api/v1/engineering-tasks", headers=headers
            )
            visible_ids = {s["task_id"] for s in list_response.json()}

            for task_id in task_ids:
                detail_response = await engineering_task_client.get(
                    f"/api/v1/engineering-tasks/{task_id}", headers=headers
                )
                if task_id in visible_ids:
                    assert detail_response.status_code == 200, (
                        f"{task_id} was in the list but detail returned "
                        f"{detail_response.status_code}"
                    )
                else:
                    assert detail_response.status_code == 404, (
                        f"{task_id} was NOT in the list but detail returned "
                        f"{detail_response.status_code}"
                    )


class TestRuntimeParameterInjectionEndToEnd:
    """Phase 9 — proves the runtime-injection design works through the
    REAL HTTP/API path (`_CapturingGraphTool`), complementing the
    lower-level, more surgical tests in
    `tests/integration/test_control_plane_authorization_integration.py`.
    """

    async def test_db_and_user_id_reach_the_tool_end_to_end(self, db_session: AsyncSession) -> None:
        _CapturingGraphTool.received_inputs.clear()
        app = _override_app(db_session, tool_cls=_CapturingGraphTool)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers, user_id = await _register_and_login(client)
            response = await client.post(
                "/api/v1/engineering-tasks",
                headers=headers,
                json={"description": "find repositories", "postconditions": ["found"]},
            )
        app.dependency_overrides.clear()

        assert response.status_code == 201, response.text
        # Two dispatches happened (generator + verifier) — both must have
        # received the injected runtime parameters.
        assert len(_CapturingGraphTool.received_inputs) == 2
        for received in _CapturingGraphTool.received_inputs:
            assert received.parameters["db"] is db_session
            assert received.parameters["user_id"] == user_id

    async def test_two_users_each_get_their_own_user_id_never_the_others(
        self, db_session: AsyncSession
    ) -> None:
        _CapturingGraphTool.received_inputs.clear()
        app = _override_app(db_session, tool_cls=_CapturingGraphTool)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers_a, user_id_a = await _register_and_login_as(client, "runtime-a@example.com")
            headers_b, user_id_b = await _register_and_login_as(client, "runtime-b@example.com")
            assert user_id_a != user_id_b

            await client.post(
                "/api/v1/engineering-tasks",
                headers=headers_a,
                json={"description": "task A", "postconditions": ["found"]},
            )
            _CapturingGraphTool.received_inputs.clear()
            await client.post(
                "/api/v1/engineering-tasks",
                headers=headers_b,
                json={"description": "task B", "postconditions": ["found"]},
            )
        app.dependency_overrides.clear()

        assert len(_CapturingGraphTool.received_inputs) == 2
        for received in _CapturingGraphTool.received_inputs:
            assert received.parameters["user_id"] == user_id_b
            assert received.parameters["user_id"] != user_id_a
