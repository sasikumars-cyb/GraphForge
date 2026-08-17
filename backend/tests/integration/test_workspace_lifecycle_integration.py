"""Real-Postgres proof of the Phase 4 Workspace lifecycle (Cap §19):

    ControlPlane -> WorkspaceLifecycleService -> Engineering State
                                               -> physical workspace backend

Covers the full lifecycle durably recorded as Engineering State events,
the Policy-governed concurrency cap (including a genuine two-connection
race), authorization-loss/custodial-destruction semantics, diagnostic
hold precedence, crash-window durability, and replay from a fresh
session — following the exact rigor established by the Phase 3 exit
audit and its corrections.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.registry import CapabilityRegistry
from app.control_plane.control_plane import ControlPlane
from app.control_plane.policy import PolicyScopeLevel, PolicyStore, seed_system_workspace_cap
from app.control_plane.workspace_lifecycle import (
    WorkspaceAuthorizationRevokedError,
    WorkspaceCapExceededError,
    WorkspaceCustodialOwnershipError,
    WorkspaceLifecycleService,
)
from app.control_plane.workspace_model import (
    DestructionReason,
    WorkspaceLease,
    WorkspaceLifecycleError,
    WorkspaceState,
)
from app.engineering_state import events as ev
from app.repositories.engineering_event_repository import EngineeringEventRepository
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

_INITIAL_LEASE_SECONDS = 900
_MAX_LIFETIME_SECONDS = 3600


def _empty_capability_registry() -> CapabilityRegistry:
    # Workspace lifecycle never touches Capabilities/Grants — an empty
    # registry is sufficient to satisfy ControlPlane's constructor.
    return CapabilityRegistry(tool_registry=ToolRegistry())


def _policy_store_with_cap(cap: int) -> PolicyStore:
    store = PolicyStore()
    store.load(
        PolicyScopeLevel.SYSTEM,
        seed_system_workspace_cap(cap, authored_by="ops", effective_at="2026-08-17T00:00:00Z"),
    )
    return store


def _control_plane(db_session: AsyncSession, *, cap: int = 3) -> ControlPlane:
    return ControlPlane(
        capability_registry=_empty_capability_registry(),
        tool_executor=ToolExecutor(registry=ToolRegistry()),
        policy_store=_policy_store_with_cap(cap),
        event_repository=EngineeringEventRepository(db_session),
    )


async def _create(
    control_plane: ControlPlane,
    *,
    task_id: uuid.UUID | None = None,
    actor: str = "dependency_query_agent",
    user_id: uuid.UUID | None = None,
    repository_url: str | None = None,
) -> tuple[WorkspaceLease, uuid.UUID]:
    return await control_plane.create_workspace(
        task_id=task_id or uuid.uuid4(),
        actor=actor,
        user_id=user_id or uuid.uuid4(),
        execution_context={"revision": "abc123"},
        repository_url=repository_url,
        initial_lease_seconds=_INITIAL_LEASE_SECONDS,
        max_lifetime_seconds=_MAX_LIFETIME_SECONDS,
    )


class TestLifecycle:
    async def test_create_records_durable_state(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        lease, created_event_id = await _create(control_plane, task_id=task_id)

        assert lease.state == WorkspaceState.LEASED
        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert len(events) == 1
        assert events[0].event_type == ev.WORKSPACE_CREATED
        assert events[0].id == created_event_id
        assert events[0].payload["actor"] == "dependency_query_agent"

    async def test_renew_extends_expiry_and_is_durable(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        lease, created_event_id = await _create(control_plane, task_id=task_id)

        new_expiry = lease.created_at + timedelta(seconds=1800)
        record = await control_plane.renew_workspace_lease(
            task_id=task_id, workspace_event_id=created_event_id, new_expires_at=new_expiry
        )
        assert record.renewal_count == 1
        assert record.lease_expires_at == new_expiry.isoformat()

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert [e.event_type for e in events] == [ev.WORKSPACE_CREATED, ev.WORKSPACE_LEASE_RENEWED]

    async def test_renewal_beyond_ceiling_denied(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        lease, created_event_id = await _create(control_plane, task_id=task_id)

        beyond_ceiling = lease.created_at + timedelta(seconds=_MAX_LIFETIME_SECONDS + 1)
        with pytest.raises(WorkspaceLifecycleError, match="exceeds the total-lifetime ceiling"):
            await control_plane.renew_workspace_lease(
                task_id=task_id, workspace_event_id=created_event_id, new_expires_at=beyond_ceiling
            )

    async def test_destroy_reaches_final_state(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        _, created_event_id = await _create(control_plane, task_id=task_id)

        record = await control_plane.destroy_workspace(
            task_id=task_id,
            workspace_event_id=created_event_id,
            reason=DestructionReason.COMPLETED_SUCCESS,
        )
        assert record.state == "destroyed"
        assert record.destruction_reason == "completed_success"

        from app.control_plane.workspace_physical import physical_workspace_exists

        assert not physical_workspace_exists(record.physical_location)


class TestPolicyCap:
    async def test_creation_within_cap_succeeds(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session, cap=2)
        actor, user_id = "agent-a", uuid.uuid4()
        await _create(control_plane, actor=actor, user_id=user_id)
        await _create(control_plane, actor=actor, user_id=user_id)

    async def test_creation_at_cap_is_denied(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session, cap=1)
        actor, user_id = "agent-a", uuid.uuid4()
        await _create(control_plane, actor=actor, user_id=user_id)
        with pytest.raises(WorkspaceCapExceededError):
            await _create(control_plane, actor=actor, user_id=user_id)

    async def test_no_cap_configured_fails_closed(self, db_session: AsyncSession) -> None:
        control_plane = ControlPlane(
            capability_registry=_empty_capability_registry(),
            tool_executor=ToolExecutor(registry=ToolRegistry()),
            policy_store=PolicyStore(),  # nothing loaded.
            event_repository=EngineeringEventRepository(db_session),
        )
        with pytest.raises(WorkspaceCapExceededError, match="fail-closed"):
            await _create(control_plane)

    async def test_different_actors_have_independent_caps(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session, cap=1)
        user_id = uuid.uuid4()
        await _create(control_plane, actor="agent-a", user_id=user_id)
        # A different actor, same user_id, is a different (Role, tenant)
        # pair and gets its own independent cap allowance.
        await _create(control_plane, actor="agent-b", user_id=user_id)

    async def test_destroyed_workspace_frees_the_cap(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session, cap=1)
        actor, user_id = "agent-a", uuid.uuid4()
        task_id = uuid.uuid4()
        _, created_event_id = await _create(
            control_plane, task_id=task_id, actor=actor, user_id=user_id
        )
        await control_plane.destroy_workspace(
            task_id=task_id,
            workspace_event_id=created_event_id,
            reason=DestructionReason.COMPLETED_SUCCESS,
        )
        # The cap is freed — a second creation for the same (actor, user_id)
        # now succeeds.
        await _create(control_plane, actor=actor, user_id=user_id)


class TestAuthorizationLoss:
    async def test_revoke_then_renew_is_denied(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        lease, created_event_id = await _create(control_plane, task_id=task_id)
        await control_plane.revoke_workspace_write_authorization(
            task_id=task_id, workspace_event_id=created_event_id, reason="policy tightened"
        )
        with pytest.raises(WorkspaceAuthorizationRevokedError):
            await control_plane.renew_workspace_lease(
                task_id=task_id,
                workspace_event_id=created_event_id,
                new_expires_at=lease.created_at + timedelta(seconds=1000),
            )

    async def test_revocation_is_durable_across_a_fresh_reconstruction(
        self, db_session: AsyncSession
    ) -> None:
        """Proves the check is against DURABLE state, not an in-memory
        flag: a brand new WorkspaceLifecycleService, backed by the same
        session, still refuses — nothing about the refusal depends on
        any Python object surviving between calls."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        lease, created_event_id = await _create(control_plane, task_id=task_id)
        await control_plane.revoke_workspace_write_authorization(
            task_id=task_id, workspace_event_id=created_event_id, reason="x"
        )

        fresh_service = WorkspaceLifecycleService(
            event_repository=EngineeringEventRepository(db_session),
            policy_store=_policy_store_with_cap(3),
        )
        with pytest.raises(WorkspaceAuthorizationRevokedError):
            await fresh_service.enter_diagnostic_hold(
                task_id=task_id,
                workspace_event_id=created_event_id,
                reason="x",
                hold_ttl_seconds=60,
            )

    async def test_custodial_destroy_succeeds_after_authorization_loss(
        self, db_session: AsyncSession
    ) -> None:
        """The audit's specific concern: custodial destruction MUST work
        precisely when the normal write path is broken."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        actor, user_id = "dependency_query_agent", uuid.uuid4()
        _, created_event_id = await _create(
            control_plane, task_id=task_id, actor=actor, user_id=user_id
        )
        await control_plane.revoke_workspace_write_authorization(
            task_id=task_id, workspace_event_id=created_event_id, reason="x"
        )

        record = await control_plane.custodial_destroy_workspace(
            task_id=task_id, workspace_event_id=created_event_id, actor=actor, user_id=user_id
        )
        assert record.state == "destroyed"
        assert record.destruction_reason == "custodial"

    async def test_normal_destroy_after_authorization_loss_is_denied(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        _, created_event_id = await _create(control_plane, task_id=task_id)
        await control_plane.revoke_workspace_write_authorization(
            task_id=task_id, workspace_event_id=created_event_id, reason="x"
        )
        with pytest.raises(WorkspaceAuthorizationRevokedError):
            await control_plane.destroy_workspace(
                task_id=task_id,
                workspace_event_id=created_event_id,
                reason=DestructionReason.COMPLETED_SUCCESS,
            )

    async def test_custodial_destroy_by_non_owner_is_denied(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        _, created_event_id = await _create(
            control_plane, task_id=task_id, actor="owner-agent", user_id=uuid.uuid4()
        )
        with pytest.raises(WorkspaceCustodialOwnershipError):
            await control_plane.custodial_destroy_workspace(
                task_id=task_id,
                workspace_event_id=created_event_id,
                actor="different-agent",
                user_id=uuid.uuid4(),
            )


class TestDiagnosticHold:
    async def test_enter_hold_records_durable_state(self, db_session: AsyncSession) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        _, created_event_id = await _create(control_plane, task_id=task_id)
        record = await control_plane.enter_diagnostic_hold(
            task_id=task_id,
            workspace_event_id=created_event_id,
            reason="task failed",
            hold_ttl_seconds=600,
        )
        assert record.state == "diagnostic_hold"

    async def test_credential_incident_destroys_immediately_from_within_hold(
        self, db_session: AsyncSession
    ) -> None:
        """§19 precedence, proven not assumed: "Diagnostic hold yields to
        data classification" — a credential incident overrides an
        active hold."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        _, created_event_id = await _create(control_plane, task_id=task_id)
        await control_plane.enter_diagnostic_hold(
            task_id=task_id, workspace_event_id=created_event_id, reason="x", hold_ttl_seconds=600
        )
        record = await control_plane.destroy_workspace(
            task_id=task_id,
            workspace_event_id=created_event_id,
            reason=DestructionReason.CREDENTIAL_INCIDENT,
        )
        assert record.state == "destroyed"
        assert record.destruction_reason == "credential_incident"

    async def test_ordinary_destroy_reason_from_within_hold_is_allowed(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        _, created_event_id = await _create(control_plane, task_id=task_id)
        await control_plane.enter_diagnostic_hold(
            task_id=task_id, workspace_event_id=created_event_id, reason="x", hold_ttl_seconds=600
        )
        record = await control_plane.destroy_workspace(
            task_id=task_id,
            workspace_event_id=created_event_id,
            reason=DestructionReason.DIAGNOSTIC_HOLD_EXPIRED,
        )
        assert record.state == "destroyed"


class TestReplay:
    async def test_full_lifecycle_reconstructs_from_a_fresh_query(
        self, db_session: AsyncSession
    ) -> None:
        from app.engineering_state.materialize import fold

        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        lease, created_event_id = await _create(control_plane, task_id=task_id)
        await control_plane.renew_workspace_lease(
            task_id=task_id,
            workspace_event_id=created_event_id,
            new_expires_at=lease.created_at + timedelta(seconds=1200),
        )
        await control_plane.destroy_workspace(
            task_id=task_id,
            workspace_event_id=created_event_id,
            reason=DestructionReason.COMPLETED_SUCCESS,
        )

        repo = EngineeringEventRepository(db_session)
        replayed_events = await repo.list_for_task(task_id)
        state = fold(replayed_events)
        assert len(state.workspaces) == 1
        record = state.workspaces[0]
        assert record.state == "destroyed"
        assert record.renewal_count == 1
        assert record.destruction_reason == "completed_success"


class TestIsolation:
    async def test_same_actor_different_tasks_get_independent_workspaces(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session, cap=5)
        actor, user_id = "agent-a", uuid.uuid4()
        lease_a, event_a = await _create(control_plane, actor=actor, user_id=user_id)
        lease_b, event_b = await _create(control_plane, actor=actor, user_id=user_id)
        assert event_a != event_b
        assert lease_a.workspace_id != lease_b.workspace_id
        assert lease_a.physical_location != lease_b.physical_location

    async def test_repeated_creation_never_aliases_an_existing_workspace(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session, cap=5)
        task_id = uuid.uuid4()
        _, event_1 = await _create(control_plane, task_id=task_id)
        _, event_2 = await _create(control_plane, task_id=task_id)
        assert event_1 != event_2

    async def test_operations_on_one_workspace_do_not_affect_another(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session, cap=5)
        actor, user_id = "agent-a", uuid.uuid4()
        task_a, task_b = uuid.uuid4(), uuid.uuid4()
        _, event_a = await _create(control_plane, task_id=task_a, actor=actor, user_id=user_id)
        _, event_b = await _create(control_plane, task_id=task_b, actor=actor, user_id=user_id)

        await control_plane.destroy_workspace(
            task_id=task_a, workspace_event_id=event_a, reason=DestructionReason.COMPLETED_SUCCESS
        )

        repo = EngineeringEventRepository(db_session)
        events_b = await repo.list_for_task(task_b)
        assert [e.event_type for e in events_b] == [ev.WORKSPACE_CREATED]


async def _create_custom(
    control_plane: ControlPlane,
    *,
    task_id: uuid.UUID | None = None,
    actor: str = "dependency_query_agent",
    user_id: uuid.UUID | None = None,
    initial_lease_seconds: int = _INITIAL_LEASE_SECONDS,
    max_lifetime_seconds: int = _MAX_LIFETIME_SECONDS,
) -> tuple[WorkspaceLease, uuid.UUID]:
    return await control_plane.create_workspace(
        task_id=task_id or uuid.uuid4(),
        actor=actor,
        user_id=user_id or uuid.uuid4(),
        execution_context={},
        repository_url=None,
        initial_lease_seconds=initial_lease_seconds,
        max_lifetime_seconds=max_lifetime_seconds,
    )


class TestConcurrentCreationAtCap:
    """Real, independent `AsyncSessionLocal()` connections — not the
    shared, savepoint-mode `db_session` fixture, exactly the standard
    the Phase 3 exit audit established: a single `AsyncSession` cannot
    express true concurrency, and a second connection racing against
    `db_session`'s never-truly-committed outer transaction would simply
    hang on the advisory lock forever."""

    async def test_two_concurrent_creations_at_cap_one_only_one_succeeds(self) -> None:
        from app.database.session import AsyncSessionLocal

        actor, user_id = f"race-actor-{uuid.uuid4()}", uuid.uuid4()

        async def _attempt() -> object:
            async with AsyncSessionLocal() as session:
                control_plane = _control_plane(session, cap=1)
                result = await _create_custom(control_plane, actor=actor, user_id=user_id)
                await session.commit()
                return result

        results = await asyncio.gather(_attempt(), _attempt(), return_exceptions=True)
        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(successes) == 1, f"expected exactly one success, got: {results}"
        assert len(failures) == 1
        assert isinstance(failures[0], WorkspaceCapExceededError)

        async with AsyncSessionLocal() as verify_session:
            from app.engineering_state.materialize import fold

            repo = EngineeringEventRepository(verify_session)
            all_events = await repo.list_by_event_types(frozenset({ev.WORKSPACE_CREATED}))
            matching = [e for e in all_events if e.payload.get("actor") == actor]
            assert len(matching) == 1, (
                f"expected exactly one durably committed WorkspaceCreated for "
                f"actor={actor!r}, found {len(matching)}"
            )
            task_events = await repo.list_for_task(matching[0].task_id)
            state = fold(task_events)
            assert len(state.workspaces) == 1


class TestCrashWindowDurability:
    async def test_created_event_survives_a_failure_during_physical_creation(self) -> None:
        """Case B/C from Phase 4 design: `WorkspaceCreated` is committed
        BEFORE the physical clone runs — a failure during that physical
        step must self-heal (a Destroyed(creation_failed) event, in the
        SAME call) rather than leave durable state silently claiming a
        Workspace that was never successfully created."""
        from app.database.session import AsyncSessionLocal

        task_id = uuid.uuid4()
        async with AsyncSessionLocal() as session:
            control_plane = _control_plane(session)
            with pytest.raises(WorkspaceLifecycleError, match="Workspace creation failed"):
                await control_plane.create_workspace(
                    task_id=task_id,
                    actor="agent-a",
                    user_id=uuid.uuid4(),
                    execution_context={},
                    # A URL `run_git_clone` will genuinely fail against
                    # (no such host resolves) — a real, synchronously
                    # caught failure, not a mock.
                    repository_url="https://this-host-does-not-exist.invalid/x/y",
                    ref="main",
                    initial_lease_seconds=_INITIAL_LEASE_SECONDS,
                    max_lifetime_seconds=_MAX_LIFETIME_SECONDS,
                )
            await session.commit()

        async with AsyncSessionLocal() as verify_session:
            repo = EngineeringEventRepository(verify_session)
            events = await repo.list_for_task(task_id)
            event_types = [e.event_type for e in events]
            assert event_types == [ev.WORKSPACE_CREATED, ev.WORKSPACE_DESTROYED]
            assert events[1].payload["reason"] == "creation_failed"

    async def test_normal_successful_path_commits_the_full_lifecycle(self) -> None:
        from app.database.session import AsyncSessionLocal

        task_id = uuid.uuid4()
        async with AsyncSessionLocal() as session:
            control_plane = _control_plane(session)
            lease, created_event_id = await _create_custom(control_plane, task_id=task_id)
            await session.commit()
            assert lease.state == WorkspaceState.LEASED

        async with AsyncSessionLocal() as verify_session:
            repo = EngineeringEventRepository(verify_session)
            events = await repo.list_for_task(task_id)
            assert [e.event_type for e in events] == [ev.WORKSPACE_CREATED]


class TestSweep:
    async def test_sweep_reclaims_an_expired_leased_workspace(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        lease, created_event_id = await _create_custom(
            control_plane, task_id=task_id, initial_lease_seconds=1
        )

        far_future = lease.lease_expires_at + timedelta(seconds=10)
        result = await control_plane.run_workspace_sweep(now=far_future)
        # `run_sweep` is deliberately system-wide (Cap §19's orphan sweep
        # reconciles the whole deployment, not one task) — asserting an
        # exact global count would be flaky under a shared dev database
        # that other tests in this same run may have left real,
        # committed Workspace rows in (via real AsyncSessionLocal()
        # sessions elsewhere in this file, which do not roll back). The
        # real, specific proof is the task-scoped check below.
        assert result["reclaimed"] >= 1

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert events[-1].event_type == ev.WORKSPACE_DESTROYED
        assert events[-1].payload["reason"] == "lease_expired_reclaimed"

    async def test_sweep_does_not_reclaim_an_unexpired_workspace(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        lease, _ = await _create_custom(control_plane, task_id=task_id, initial_lease_seconds=900)

        await control_plane.run_workspace_sweep(now=lease.created_at)

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert [e.event_type for e in events] == [ev.WORKSPACE_CREATED]

    async def test_sweep_does_not_reclaim_a_held_workspace_even_if_lease_would_be_expired(
        self, db_session: AsyncSession
    ) -> None:
        """§19: "Expiry MUST NOT destroy a Workspace under an active
        hold" — diagnostic hold outranks ordinary lease expiry."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        lease, created_event_id = await _create_custom(
            control_plane, task_id=task_id, initial_lease_seconds=1
        )
        await control_plane.enter_diagnostic_hold(
            task_id=task_id, workspace_event_id=created_event_id, reason="x", hold_ttl_seconds=600
        )

        far_future = lease.lease_expires_at + timedelta(seconds=10)
        await control_plane.run_workspace_sweep(now=far_future)

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert [e.event_type for e in events] == [
            ev.WORKSPACE_CREATED,
            ev.WORKSPACE_DIAGNOSTIC_HOLD_ENTERED,
        ]

    async def test_sweep_destroys_a_workspace_whose_diagnostic_hold_ttl_has_expired(
        self, db_session: AsyncSession
    ) -> None:
        """Found during Phase 4's own exit self-audit: §19's disposition
        table says "retained under diagnostic hold with bounded TTL,
        THEN DESTROYED" — a distinct expiry mechanism from lease-TTL
        expiry that the sweep must also reconcile."""
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        _, created_event_id = await _create_custom(control_plane, task_id=task_id)
        held = await control_plane.enter_diagnostic_hold(
            task_id=task_id, workspace_event_id=created_event_id, reason="x", hold_ttl_seconds=1
        )
        from datetime import datetime as _dt

        assert held.diagnostic_hold_expires_at is not None
        hold_expires_at = _dt.fromisoformat(held.diagnostic_hold_expires_at)
        result = await control_plane.run_workspace_sweep(
            now=hold_expires_at + timedelta(seconds=10)
        )
        assert result["reclaimed"] >= 1

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert events[-1].event_type == ev.WORKSPACE_DESTROYED
        assert events[-1].payload["reason"] == "diagnostic_hold_expired"

    async def test_hold_expired_workspace_does_not_permanently_exhaust_the_cap(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session, cap=1)
        actor, user_id = "agent-a", uuid.uuid4()
        task_id = uuid.uuid4()
        _, created_event_id = await _create_custom(
            control_plane, task_id=task_id, actor=actor, user_id=user_id
        )
        await control_plane.enter_diagnostic_hold(
            task_id=task_id, workspace_event_id=created_event_id, reason="x", hold_ttl_seconds=1
        )
        import asyncio as _asyncio

        await _asyncio.sleep(1.1)
        # A second creation for the same (actor, user_id) succeeds even
        # though the first Workspace's Destroyed event was never
        # appended — the count query itself treats a hold-expired
        # Workspace as non-counting.
        await _create_custom(control_plane, actor=actor, user_id=user_id)

    async def test_sweep_retries_physical_cleanup_for_a_destroyed_but_leftover_workspace(
        self, db_session: AsyncSession
    ) -> None:
        from app.control_plane import workspace_physical

        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        _, created_event_id = await _create_custom(control_plane, task_id=task_id)
        record = await control_plane.destroy_workspace(
            task_id=task_id,
            workspace_event_id=created_event_id,
            reason=DestructionReason.COMPLETED_SUCCESS,
        )
        assert not workspace_physical.physical_workspace_exists(record.physical_location)

        # Simulate Case D: physical cleanup previously failed — recreate
        # the directory by hand, exactly as a failed `rmtree` would leave
        # it, WITHOUT any new durable event (Destroyed is already final).
        import os

        os.makedirs(record.physical_location, exist_ok=True)
        assert workspace_physical.physical_workspace_exists(record.physical_location)

        result = await control_plane.run_workspace_sweep()
        assert result["physically_cleaned"] >= 1
        assert not workspace_physical.physical_workspace_exists(record.physical_location)

        # No new Engineering Event was appended for the sweep's cleanup —
        # Destroyed was already the authoritative, final record.
        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        assert [e.event_type for e in events] == [ev.WORKSPACE_CREATED, ev.WORKSPACE_DESTROYED]

    async def test_sweep_is_idempotent_on_an_already_reconciled_workspace(
        self, db_session: AsyncSession
    ) -> None:
        control_plane = _control_plane(db_session)
        task_id = uuid.uuid4()
        lease, created_event_id = await _create_custom(
            control_plane, task_id=task_id, initial_lease_seconds=1
        )
        far_future = lease.lease_expires_at + timedelta(seconds=10)

        await control_plane.run_workspace_sweep(now=far_future)
        await control_plane.run_workspace_sweep(now=far_future)

        repo = EngineeringEventRepository(db_session)
        events = await repo.list_for_task(task_id)
        # Exactly one Destroyed — the second sweep pass did not append
        # a duplicate.
        assert [e.event_type for e in events].count(ev.WORKSPACE_DESTROYED) == 1

    async def test_two_independent_sessions_sweeping_concurrently_reclaim_exactly_once(
        self,
    ) -> None:
        from app.database.session import AsyncSessionLocal

        task_id = uuid.uuid4()
        async with AsyncSessionLocal() as session:
            control_plane = _control_plane(session)
            lease, created_event_id = await _create_custom(
                control_plane, task_id=task_id, initial_lease_seconds=1
            )
            await session.commit()

        far_future = lease.lease_expires_at + timedelta(seconds=10)

        async def _sweep() -> dict[str, int]:
            async with AsyncSessionLocal() as session:
                control_plane = _control_plane(session)
                result = await control_plane.run_workspace_sweep(now=far_future)
                await session.commit()
                return result

        await asyncio.gather(_sweep(), _sweep())

        async with AsyncSessionLocal() as verify_session:
            repo = EngineeringEventRepository(verify_session)
            events = await repo.list_for_task(task_id)
            assert [e.event_type for e in events].count(ev.WORKSPACE_DESTROYED) == 1
