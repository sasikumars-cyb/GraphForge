"""Real-Postgres proof of the Phase 3 authorization path:

    ActionProposal -> ControlPlane -> Conformance -> Capability -> Scope
    -> Policy -> Safety Validity -> Authorization -> Authorization Grant
    -> ToolExecutor -> Tool

Covers the full Grant lifecycle durably recorded as Engineering State
events (Cap §7.1), the final-gate denial paths (each recorded, never
silently dropped), the crash-safe three-state Grant lifecycle, and a
handful of the required adversarial attacks (Grant reuse, expired Grant,
wrong Policy). Structural/no-DB checks (`check_conformance`,
`check_eligibility`) live in
`tests/unit/control_plane/test_control_plane_pipeline.py` instead.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
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
from app.control_plane.control_plane import AuthorizationDeniedError, ControlPlane
from app.control_plane.grant import AuthorizationGrant, GrantLifecycleError, GrantState
from app.control_plane.model import Action, Prediction
from app.control_plane.policy import (
    PolicyRule,
    PolicyRuleEffect,
    PolicyScopeLevel,
    PolicyStore,
    seed_system_policy_allowing,
)
from app.engineering_state import events as ev
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
            data={"data": {"repositories": []}, "summary": "0 repositories found"},
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


def _action(**overrides: object) -> Action:
    defaults: dict[str, object] = {
        "action_id": uuid.uuid4(),
        "capability_id": "query_knowledge_graph",
        "capability_version": 1,
        "parameters": {"query": "find repos", "parameters": {}},
        "prediction": Prediction(
            target_observable="data",
            falsification_condition="data is empty",
            evaluation_procedure="check keys",
            execution_context={},
            necessary_condition_rationale="needed for the plan step",
        ),
        "plan_step_id": uuid.uuid4(),
    }
    defaults.update(overrides)
    return Action(**defaults)  # type: ignore[arg-type]


class TestHappyPath:
    async def test_authorized_action_dispatches_and_records_full_lifecycle(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action = _action()

        result = await control_plane.authorize_and_execute(
            task_id=task_id, action=action, human_approval=None
        )

        assert result.outcome == "completed"
        assert result.tool_success is True

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        event_types = [e.event_type for e in events]
        assert event_types == [
            ev.AUTHORIZATION_GRANTED,
            ev.AUTHORIZATION_CONSUMING,
            ev.AUTHORIZATION_CONSUMED,
            ev.OBSERVATION_RECORDED,
        ]

        granted = events[0]
        assert granted.payload["action_id"] == str(action.action_id)
        assert granted.payload["capability_id"] == "query_knowledge_graph"
        assert granted.payload["safety_validity_result"]["valid"] is True

        consuming = events[1]
        assert consuming.causation_event_id == granted.id

        consumed = events[2]
        assert consumed.causation_event_id == consuming.id
        assert consumed.payload["grant_event_id"] == str(granted.id)

        observation = events[3]
        assert observation.payload["success"] is True
        assert observation.payload["grant_id"] == granted.payload["grant_id"]

    async def test_authorization_is_never_cited_as_evidence(self, db_session: AsyncSession) -> None:
        """Cap §7.1: "Authorization is permission, never evidence." A
        BeliefRecorded citing an AuthorizationGranted event as evidence
        must be rejected the same way it rejects citing a non-Evidence
        event today."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action = _action()
        await control_plane.authorize_and_execute(
            task_id=task_id, action=action, human_approval=None
        )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        granted_event = events[0]

        from app.repositories.engineering_event_repository import CausalOrderViolationError

        with pytest.raises(CausalOrderViolationError):
            await repo.append(
                task_id=task_id,
                event_type=ev.BELIEF_RECORDED,
                payload={
                    "proposition": "the graph query is trustworthy",
                    "confidence": 0.9,
                    "uncertainty": "low",
                    "evidence_sufficiency": "adequate",
                    "qualitative_status": "corroborated",
                    "derivation_method": "direct observation",
                    "evidence_ids": [str(granted_event.id)],
                },
                actor="test:harness",
            )


class TestDenialPaths:
    async def test_unknown_capability_is_denied_and_recorded(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action = _action(capability_id="does_not_exist")

        from app.control_plane.control_plane import CapabilityGapError

        with pytest.raises(CapabilityGapError):
            await control_plane.authorize_and_execute(
                task_id=task_id, action=action, human_approval=None
            )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert len(events) == 1
        assert events[0].event_type == ev.AUTHORIZATION_DENIED
        assert events[0].payload["denial_stage"] == "capability_gap"

    async def test_policy_denial_is_recorded_and_no_grant_issued(
        self, db_session: AsyncSession
    ) -> None:
        store = PolicyStore()  # nothing loaded — fail-closed default-deny.
        control_plane = _control_plane(db_session, policy_store=store)
        task_id = uuid.uuid4()
        action = _action()

        with pytest.raises(AuthorizationDeniedError):
            await control_plane.authorize_and_execute(
                task_id=task_id, action=action, human_approval=None
            )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert len(events) == 1
        assert events[0].event_type == ev.AUTHORIZATION_DENIED
        assert events[0].payload["denial_stage"] == "policy_denial"

    async def test_narrower_scope_deny_overrides_broader_allow(
        self, db_session: AsyncSession
    ) -> None:
        store = _seeded_policy_store()
        store.load(
            PolicyScopeLevel.TASK,
            _deny_version("query_knowledge_graph"),
        )
        control_plane = _control_plane(db_session, policy_store=store)
        task_id = uuid.uuid4()
        action = _action()

        with pytest.raises(AuthorizationDeniedError):
            await control_plane.authorize_and_execute(
                task_id=task_id, action=action, human_approval=None
            )

    async def test_emergency_policy_denies_even_a_known_safe_action(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action = _action()

        with pytest.raises(AuthorizationDeniedError):
            await control_plane.authorize_and_execute(
                task_id=task_id,
                action=action,
                human_approval=None,
                emergency_policy_active=True,
            )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert events[0].payload["denial_stage"] == "stale_safety_validity"

    async def test_lease_conflict_denies(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action = _action()

        with pytest.raises(AuthorizationDeniedError):
            await control_plane.authorize_and_execute(
                task_id=task_id, action=action, human_approval=None, lease_conflicts=True
            )


class TestAdversarialGrantAttacks:
    async def test_expired_grant_cannot_be_consumed(self, db_session: AsyncSession) -> None:
        """Directly attacks the Grant object, bypassing
        `authorize_and_execute`'s normal issuance path — proves the
        expiry check is real, not merely "never happens to be hit."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action = _action()

        import dataclasses

        grant, granted_event_id = await control_plane._issue_grant(
            task_id=task_id,
            action=action,
            capability=_capability_registry().get("query_knowledge_graph", 1),  # type: ignore[arg-type]
            policy_version_id="policy-x",
            safety_valid=True,
            safety_reason="ok",
            human_approval=None,
        )
        expired_grant = dataclasses.replace(
            grant, issued_at=datetime.now(UTC) - timedelta(seconds=1000)
        )

        with pytest.raises(AuthorizationDeniedError):
            await control_plane._consume_and_dispatch(
                task_id=task_id,
                action=action,
                grant=expired_grant,
                granted_event_id=granted_event_id,
            )

    async def test_grant_cannot_be_consumed_twice(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action = _action()

        result = await control_plane.authorize_and_execute(
            task_id=task_id, action=action, human_approval=None
        )
        assert result.outcome == "completed"

        # Reconstruct the now-CONSUMED grant object exactly as issued and
        # try to consume it again — Cap §7: "never be reused."
        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        granted_event = events[0]

        stale_grant = AuthorizationGrant(
            grant_id=uuid.UUID(granted_event.payload["grant_id"]),
            action_id=action.action_id,
            capability_id="query_knowledge_graph",
            capability_version=1,
            action_parameters_hash=granted_event.payload["action_parameters_hash"],
            policy_version_id=granted_event.payload["policy_version_id"],
            scope=granted_event.payload["scope"],
            safety_validity_result="ok",
            safety_validity_valid=True,
            novelty="known",
            human_approval_content_hash=None,
            issued_at=datetime.fromisoformat(granted_event.payload["issued_at"]),
            ttl_seconds=granted_event.payload["ttl_seconds"],
            state=GrantState.CONSUMED,
        )
        with pytest.raises(GrantLifecycleError):
            stale_grant.consuming()


class TestGrantActionIdentityBinding:
    """Phase 3 exit-audit correction #2 — `_consume_and_dispatch` must
    refuse to consume a Grant against an Action it was not issued for,
    including when only the parameters differ."""

    async def test_grant_a_with_action_a_succeeds(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action = _action()

        result = await control_plane.authorize_and_execute(
            task_id=task_id, action=action, human_approval=None
        )
        assert result.outcome == "completed"

    async def test_grant_a_with_a_different_action_id_is_denied(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action_a = _action()
        capability = _capability_registry().get("query_knowledge_graph", 1)
        assert capability is not None

        grant, granted_event_id = await control_plane._issue_grant(
            task_id=task_id,
            action=action_a,
            capability=capability,
            policy_version_id="policy-x",
            safety_valid=True,
            safety_reason="ok",
            human_approval=None,
        )

        action_b = _action()  # a fresh action_id, everything else identical
        with pytest.raises(AuthorizationDeniedError):
            await control_plane._consume_and_dispatch(
                task_id=task_id,
                action=action_b,
                grant=grant,
                granted_event_id=granted_event_id,
            )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        event_types = [e.event_type for e in events]
        # No AuthorizationConsuming/Consumed — dispatch never happened.
        assert ev.AUTHORIZATION_CONSUMING not in event_types
        assert ev.AUTHORIZATION_CONSUMED not in event_types
        assert event_types.count(ev.AUTHORIZATION_DENIED) == 1

    async def test_grant_a_with_a_different_capability_is_denied(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action_a = _action()
        capability = _capability_registry().get("query_knowledge_graph", 1)
        assert capability is not None

        grant, granted_event_id = await control_plane._issue_grant(
            task_id=task_id,
            action=action_a,
            capability=capability,
            policy_version_id="policy-x",
            safety_valid=True,
            safety_reason="ok",
            human_approval=None,
        )

        mismatched_action = _action(
            action_id=action_a.action_id, capability_id="some_other_capability"
        )
        with pytest.raises(AuthorizationDeniedError):
            await control_plane._consume_and_dispatch(
                task_id=task_id,
                action=mismatched_action,
                grant=grant,
                granted_event_id=granted_event_id,
            )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert ev.AUTHORIZATION_CONSUMING not in [e.event_type for e in events]

    async def test_grant_a_with_a_different_capability_version_is_denied(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action_a = _action()
        capability = _capability_registry().get("query_knowledge_graph", 1)
        assert capability is not None

        grant, granted_event_id = await control_plane._issue_grant(
            task_id=task_id,
            action=action_a,
            capability=capability,
            policy_version_id="policy-x",
            safety_valid=True,
            safety_reason="ok",
            human_approval=None,
        )

        mismatched_action = _action(action_id=action_a.action_id, capability_version=2)
        with pytest.raises(AuthorizationDeniedError):
            await control_plane._consume_and_dispatch(
                task_id=task_id,
                action=mismatched_action,
                grant=grant,
                granted_event_id=granted_event_id,
            )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert ev.AUTHORIZATION_CONSUMING not in [e.event_type for e in events]

    async def test_grant_a_with_modified_action_parameters_is_denied(
        self, db_session: AsyncSession
    ) -> None:
        """The audit's specific concern: matching action_id/capability_id/
        capability_version alone is not enough — a caller could supply
        the correct identity but different `parameters`."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action_a = _action(parameters={"query": "find repos", "parameters": {}})
        capability = _capability_registry().get("query_knowledge_graph", 1)
        assert capability is not None

        grant, granted_event_id = await control_plane._issue_grant(
            task_id=task_id,
            action=action_a,
            capability=capability,
            policy_version_id="policy-x",
            safety_valid=True,
            safety_reason="ok",
            human_approval=None,
        )

        modified_action = _action(
            action_id=action_a.action_id,
            parameters={"query": "find repos AND also delete everything", "parameters": {}},
        )
        with pytest.raises(AuthorizationDeniedError):
            await control_plane._consume_and_dispatch(
                task_id=task_id,
                action=modified_action,
                grant=grant,
                granted_event_id=granted_event_id,
            )

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert ev.AUTHORIZATION_CONSUMING not in [e.event_type for e in events]


class _CountingFakeGraphTool:
    """Like `_FakeGraphTool`, but records each real dispatch into a
    caller-supplied, shared list — used by the concurrency test below to
    observe, from outside either `ControlPlane` instance, exactly how
    many times the Tool actually ran."""

    tool_id = "neo4j_graph"
    display_name = "Fake Graph"
    description = "d"
    category = ToolCategory.GRAPH
    capabilities: list[str] = []

    def __init__(self, call_log: list[str]) -> None:
        self._call_log = call_log

    async def execute(self, input: ToolInput) -> ToolResult:
        self._call_log.append("dispatched")
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={"data": {"repositories": []}, "summary": "0 repositories found"},
        )

    async def health_check(self) -> ToolHealth:
        return ToolHealth.HEALTHY

    def requires_auth(self) -> bool:
        return False


def _counting_tool_registry(call_log: list[str]) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            tool_id="neo4j_graph",
            display_name="Fake Graph",
            description="d",
            category=ToolCategory.GRAPH,
            capabilities=[],
            factory=lambda cfg: _CountingFakeGraphTool(call_log),
            requires_auth=False,
            default_enabled=True,
        )
    )
    return registry


class TestConsumptionUniqueness:
    """Phase 3 exit-audit correction #3 — two independently reconstructed
    `AuthorizationGrant` objects, each honestly believing itself GRANTED,
    must not both be allowed to begin consumption. Uses two REAL, separate
    `AsyncSessionLocal()` connections (not the shared, savepoint-mode
    `db_session` fixture) so the advisory lock and the durable-commit
    correction genuinely serialize two independent database transactions
    — the `db_session` fixture's single shared connection cannot express
    this scenario, since a single `AsyncSession` is not safe for
    concurrent use from two coroutines, and a second real connection
    attempting the same advisory lock against `db_session`'s
    never-truly-committed outer transaction would simply hang forever.
    """

    async def test_two_independently_reconstructed_grants_concurrently_only_one_wins(
        self,
    ) -> None:
        from app.database.session import AsyncSessionLocal

        task_id = uuid.uuid4()
        action = _action()
        call_log: list[str] = []

        # Issue and durably commit the Grant for real, via its own
        # session — matching how a genuinely separate later
        # process/request would only ever see an already-committed Grant.
        async with AsyncSessionLocal() as issuing_session:
            issuing_control_plane = ControlPlane(
                capability_registry=_capability_registry(),
                tool_executor=ToolExecutor(registry=_counting_tool_registry(call_log)),
                policy_store=_seeded_policy_store(),
                event_repository=EngineeringEventRepository(issuing_session),
            )
            capability = _capability_registry().get("query_knowledge_graph", 1)
            assert capability is not None
            grant, granted_event_id = await issuing_control_plane._issue_grant(
                task_id=task_id,
                action=action,
                capability=capability,
                policy_version_id="policy-x",
                safety_valid=True,
                safety_reason="ok",
                human_approval=None,
            )
            await issuing_session.commit()

        # Two independently reconstructed Grant objects — NOT the same
        # Python object — both honestly reporting GRANTED, exactly as two
        # separate processes rehydrating the same persisted
        # AuthorizationGranted event would each independently produce.
        import dataclasses

        grant_copy_a = dataclasses.replace(grant)
        grant_copy_b = dataclasses.replace(grant)
        assert grant_copy_a is not grant_copy_b

        async def _attempt(grant_copy: AuthorizationGrant) -> Any:
            async with AsyncSessionLocal() as session:
                control_plane = ControlPlane(
                    capability_registry=_capability_registry(),
                    tool_executor=ToolExecutor(registry=_counting_tool_registry(call_log)),
                    policy_store=_seeded_policy_store(),
                    event_repository=EngineeringEventRepository(session),
                )
                result = await control_plane._consume_and_dispatch(
                    task_id=task_id,
                    action=action,
                    grant=grant_copy,
                    granted_event_id=granted_event_id,
                )
                await session.commit()
                return result

        results = await asyncio.gather(
            _attempt(grant_copy_a), _attempt(grant_copy_b), return_exceptions=True
        )

        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(successes) == 1, f"expected exactly one success, got: {results}"
        assert len(failures) == 1
        assert isinstance(failures[0], AuthorizationDeniedError)

        # Exactly one real Tool dispatch — not two.
        assert call_log == ["dispatched"]

        async with AsyncSessionLocal() as verify_session:
            repo = EngineeringEventRepository(verify_session)
            events = await repo.list_for_task(task_id)
            event_types = [e.event_type for e in events]
            assert event_types.count(ev.AUTHORIZATION_CONSUMING) == 1
            assert event_types.count(ev.AUTHORIZATION_CONSUMED) == 1


class TestCrashWindowDurability:
    """Phase 3 exit-audit correction #1 — proves `AuthorizationConsuming`
    survives a failure that occurs during/after Tool dispatch, using real,
    separately-committing `AsyncSessionLocal()` sessions (see
    `TestConsumptionUniqueness`'s docstring for why the shared `db_session`
    fixture cannot express this)."""

    async def test_consuming_survives_a_failure_during_tool_dispatch(self) -> None:
        from app.database.session import AsyncSessionLocal
        from app.engineering_state.materialize import fold

        class _CrashingToolExecutor:
            """Stands in for `app.tools.executor.ToolExecutor` directly —
            NOT a failing Tool underneath a real `ToolExecutor`, because
            `ToolExecutor.execute()` deliberately never raises (it catches
            its own exceptions and returns `ToolResult(success=False)`,
            per its own docstring). A genuine process/tool crash is
            exactly the case that never becomes a caught, well-formed
            `ToolResult` — this fake raises past that boundary, the way
            an actual process crash would."""

            async def execute(self, tool_id: str, tool_input: ToolInput) -> ToolResult:
                raise RuntimeError("simulated process/tool failure during dispatch")

        task_id = uuid.uuid4()
        action = _action()

        async with AsyncSessionLocal() as session:
            control_plane = ControlPlane(
                capability_registry=_capability_registry(),
                tool_executor=_CrashingToolExecutor(),  # type: ignore[arg-type]
                policy_store=_seeded_policy_store(),
                event_repository=EngineeringEventRepository(session),
            )
            capability = _capability_registry().get("query_knowledge_graph", 1)
            assert capability is not None
            grant, granted_event_id = await control_plane._issue_grant(
                task_id=task_id,
                action=action,
                capability=capability,
                policy_version_id="policy-x",
                safety_valid=True,
                safety_reason="ok",
                human_approval=None,
            )
            await session.commit()

            # `_consume_and_dispatch` appends+commits AuthorizationConsuming,
            # THEN calls the (failing) Tool — the RuntimeError below
            # happens strictly after correction #1's commit.
            with pytest.raises(RuntimeError, match="simulated process/tool failure"):
                await control_plane._consume_and_dispatch(
                    task_id=task_id,
                    action=action,
                    grant=grant,
                    granted_event_id=granted_event_id,
                )
            # Step 6 of the audit's crash-window scenario: roll back
            # anything left uncommitted by the failure (nothing durable
            # is lost by this — AuthorizationConsuming already committed
            # before the exception was ever raised).
            await session.rollback()

        # Step 7: re-read from a genuinely FRESH, separate session/
        # connection — not the session ControlPlane used, and not any
        # in-memory object it held.
        async with AsyncSessionLocal() as verify_session:
            repo = EngineeringEventRepository(verify_session)
            events = await repo.list_for_task(task_id)
            event_types = [e.event_type for e in events]

            assert ev.AUTHORIZATION_GRANTED in event_types
            assert ev.AUTHORIZATION_CONSUMING in event_types
            # The specific, load-bearing assertion: Consumed must NOT
            # falsely appear — the failure happened before it could be
            # appended.
            assert ev.AUTHORIZATION_CONSUMED not in event_types
            assert ev.OBSERVATION_RECORDED not in event_types

            state = fold(events)
            assert len(state.authorization_grants) == 1
            assert state.authorization_grants[0].state == "consuming"

    async def test_normal_successful_path_commits_the_full_lifecycle(self) -> None:
        """The companion positive case: Granted -> Consuming (durable) ->
        Tool dispatch -> Consumed, all reachable from a fresh session."""
        from app.database.session import AsyncSessionLocal
        from app.engineering_state.materialize import fold

        task_id = uuid.uuid4()
        action = _action()
        call_log: list[str] = []

        async with AsyncSessionLocal() as session:
            control_plane = ControlPlane(
                capability_registry=_capability_registry(),
                tool_executor=ToolExecutor(registry=_counting_tool_registry(call_log)),
                policy_store=_seeded_policy_store(),
                event_repository=EngineeringEventRepository(session),
            )
            result = await control_plane.authorize_and_execute(
                task_id=task_id, action=action, human_approval=None
            )
            await session.commit()
            assert result.outcome == "completed"

        assert call_log == ["dispatched"]

        async with AsyncSessionLocal() as verify_session:
            repo = EngineeringEventRepository(verify_session)
            events = await repo.list_for_task(task_id)
            event_types = [e.event_type for e in events]
            assert event_types == [
                ev.AUTHORIZATION_GRANTED,
                ev.AUTHORIZATION_CONSUMING,
                ev.AUTHORIZATION_CONSUMED,
                ev.OBSERVATION_RECORDED,
            ]

            state = fold(events)
            assert state.authorization_grants[0].state == "consumed"


class TestReplay:
    async def test_control_plane_history_is_reconstructable_from_engineering_state(
        self, db_session: AsyncSession
    ) -> None:
        """Engineering State contract §9's "MUST be exactly
        reconstructable" guarantee, exercised for the Phase 3 Authorization
        Grant lifecycle specifically: run a real authorization through a
        real Postgres, fetch the persisted events back out (a fresh read,
        not the in-memory objects `authorize_and_execute` already built),
        and fold them with `app.engineering_state.materialize.fold` — the
        reconstructed Grant record must match what actually happened, with
        no help from anything the Control Plane still holds in memory.
        """
        from app.engineering_state.materialize import fold

        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        action = _action()

        await control_plane.authorize_and_execute(
            task_id=task_id, action=action, human_approval=None
        )

        repo = EngineeringEventRepository(db_session)
        replayed_events = await repo.list_for_task(task_id)
        state = fold(replayed_events)

        assert len(state.authorization_grants) == 1
        grant_record = state.authorization_grants[0]
        assert grant_record.state == "consumed"
        assert grant_record.action_id == action.action_id
        assert grant_record.capability_id == "query_knowledge_graph"
        # `grant_record.grant_event_id` is the `AuthorizationGranted`
        # event's own persisted id (the first event of this task's
        # replayed stream) — distinct from `result.grant_id`, which is the
        # separate business identifier `_issue_grant` generates (see its
        # own docstring on why these are deliberately two different ids).
        assert grant_record.grant_event_id == replayed_events[0].id
        assert replayed_events[0].event_type == ev.AUTHORIZATION_GRANTED
        assert state.authorization_denials == ()

    async def test_denial_history_is_reconstructable_with_no_grant_recorded(
        self, db_session: AsyncSession
    ) -> None:
        from app.engineering_state.materialize import fold

        store = PolicyStore()  # fail-closed default-deny.
        control_plane = _control_plane(db_session, policy_store=store)
        task_id = uuid.uuid4()
        action = _action()

        with pytest.raises(AuthorizationDeniedError):
            await control_plane.authorize_and_execute(
                task_id=task_id, action=action, human_approval=None
            )

        repo = EngineeringEventRepository(db_session)
        replayed_events = await repo.list_for_task(task_id)
        state = fold(replayed_events)

        assert state.authorization_grants == ()
        assert len(state.authorization_denials) == 1
        assert state.authorization_denials[0].denial_stage == "policy_denial"


def _deny_version(capability_id: str) -> Any:
    from app.control_plane.policy import PolicyVersion

    return PolicyVersion(
        rules=(
            PolicyRule(
                capability_id=capability_id,
                effect=PolicyRuleEffect.DENY,
                scope_level=PolicyScopeLevel.TASK,
                reason="task under incident review",
            ),
        ),
        authoring_authority="incident-response",
        effective_at="2026-08-17T00:00:00Z",
        supersedes=None,
    )
