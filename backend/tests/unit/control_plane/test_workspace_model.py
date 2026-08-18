"""Contract tests for `app.control_plane.workspace_model` — the pure
state machine, no database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.control_plane.workspace_model import (
    WorkspaceLease,
    WorkspaceLifecycleError,
    WorkspaceState,
    is_within_total_lifetime,
)


def _lease(**overrides: object) -> WorkspaceLease:
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "workspace_id": uuid.uuid4(),
        "task_id": uuid.uuid4(),
        "actor": "dependency_query_agent",
        "user_id": uuid.uuid4(),
        "execution_context": {},
        "physical_location": "/tmp/graphforge-workspaces/x",
        "repository_url": "https://github.com/example/repo",
        "created_at": now,
        "max_lifetime_seconds": 3600,
        "lease_expires_at": now + timedelta(seconds=900),
        "renewal_count": 0,
    }
    defaults.update(overrides)
    return WorkspaceLease(**defaults)  # type: ignore[arg-type]


class TestIsWithinTotalLifetime:
    def test_expiry_within_ceiling_is_allowed(self) -> None:
        t0 = datetime.now(UTC)
        assert is_within_total_lifetime(
            created_at=t0, proposed_expiry=t0 + timedelta(seconds=100), max_lifetime_seconds=3600
        )

    def test_expiry_exactly_at_ceiling_is_allowed(self) -> None:
        t0 = datetime.now(UTC)
        assert is_within_total_lifetime(
            created_at=t0, proposed_expiry=t0 + timedelta(seconds=3600), max_lifetime_seconds=3600
        )

    def test_expiry_beyond_ceiling_is_rejected(self) -> None:
        t0 = datetime.now(UTC)
        assert not is_within_total_lifetime(
            created_at=t0, proposed_expiry=t0 + timedelta(seconds=3601), max_lifetime_seconds=3600
        )

    def test_ceiling_is_anchored_at_creation_not_at_previous_expiry(self) -> None:
        """The audit's own formula: E <= T0 + M, anchored at T0 — a
        renewal proposed relative to some LATER point (not T0) that
        would still land beyond T0+M must be rejected."""
        t0 = datetime.now(UTC)
        far_future_relative_but_beyond_ceiling = t0 + timedelta(seconds=3700)
        assert not is_within_total_lifetime(
            created_at=t0,
            proposed_expiry=far_future_relative_but_beyond_ceiling,
            max_lifetime_seconds=3600,
        )


class TestLeaseExpiry:
    def test_not_expired_before_lease_expires_at(self) -> None:
        lease = _lease(lease_expires_at=datetime.now(UTC) + timedelta(seconds=100))
        assert not lease.is_lease_expired(now=datetime.now(UTC))

    def test_expired_after_lease_expires_at(self) -> None:
        lease = _lease(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert lease.is_lease_expired(now=datetime.now(UTC))

    def test_hold_ttl_independent_of_lease_expiry(self) -> None:
        now = datetime.now(UTC)
        lease = _lease(
            state=WorkspaceState.DIAGNOSTIC_HOLD,
            diagnostic_hold_expires_at=now + timedelta(seconds=50),
        )
        assert not lease.is_hold_expired(now=now)
        assert lease.is_hold_expired(now=now + timedelta(seconds=51))

    def test_no_hold_expiry_when_never_held(self) -> None:
        lease = _lease()
        assert not lease.is_hold_expired(now=datetime.now(UTC) + timedelta(days=365))


class TestRenewal:
    def test_renew_from_leased_succeeds(self) -> None:
        t0 = datetime.now(UTC)
        lease = _lease(created_at=t0, max_lifetime_seconds=3600)
        renewed = lease.renewed(new_expires_at=t0 + timedelta(seconds=1800))
        assert renewed.renewal_count == 1
        assert renewed.lease_expires_at == t0 + timedelta(seconds=1800)
        assert renewed.state == WorkspaceState.LEASED

    def test_renewal_beyond_total_lifetime_ceiling_is_rejected(self) -> None:
        t0 = datetime.now(UTC)
        lease = _lease(created_at=t0, max_lifetime_seconds=3600)
        with pytest.raises(WorkspaceLifecycleError, match="exceeds the total-lifetime ceiling"):
            lease.renewed(new_expires_at=t0 + timedelta(seconds=3601))

    def test_renew_from_diagnostic_hold_is_rejected(self) -> None:
        lease = _lease(state=WorkspaceState.DIAGNOSTIC_HOLD)
        with pytest.raises(WorkspaceLifecycleError):
            lease.renewed(new_expires_at=datetime.now(UTC) + timedelta(seconds=10))

    def test_renew_from_write_authorization_revoked_is_rejected(self) -> None:
        lease = _lease(state=WorkspaceState.WRITE_AUTHORIZATION_REVOKED)
        with pytest.raises(WorkspaceLifecycleError):
            lease.renewed(new_expires_at=datetime.now(UTC) + timedelta(seconds=10))

    def test_renew_from_destroyed_is_rejected(self) -> None:
        lease = _lease(state=WorkspaceState.DESTROYED)
        with pytest.raises(WorkspaceLifecycleError):
            lease.renewed(new_expires_at=datetime.now(UTC) + timedelta(seconds=10))

    def test_original_lease_object_is_unchanged_after_renewal(self) -> None:
        lease = _lease()
        original_expiry = lease.lease_expires_at
        lease.renewed(new_expires_at=original_expiry + timedelta(seconds=10))
        assert lease.lease_expires_at == original_expiry
        assert lease.renewal_count == 0


class TestDiagnosticHold:
    def test_enter_hold_from_leased_succeeds(self) -> None:
        lease = _lease()
        held = lease.entered_diagnostic_hold(
            reason="task failed", hold_expires_at=datetime.now(UTC) + timedelta(seconds=600)
        )
        assert held.state == WorkspaceState.DIAGNOSTIC_HOLD
        assert held.diagnostic_hold_reason == "task failed"

    def test_enter_hold_from_hold_is_rejected(self) -> None:
        lease = _lease(state=WorkspaceState.DIAGNOSTIC_HOLD)
        with pytest.raises(WorkspaceLifecycleError):
            lease.entered_diagnostic_hold(
                reason="x", hold_expires_at=datetime.now(UTC) + timedelta(seconds=1)
            )

    def test_enter_hold_from_destroyed_is_rejected(self) -> None:
        lease = _lease(state=WorkspaceState.DESTROYED)
        with pytest.raises(WorkspaceLifecycleError):
            lease.entered_diagnostic_hold(
                reason="x", hold_expires_at=datetime.now(UTC) + timedelta(seconds=1)
            )


class TestWriteAuthorizationRevocation:
    def test_revoke_from_leased_succeeds(self) -> None:
        lease = _lease()
        revoked = lease.write_authorization_revoked()
        assert revoked.state == WorkspaceState.WRITE_AUTHORIZATION_REVOKED

    def test_revoke_from_hold_is_rejected(self) -> None:
        lease = _lease(state=WorkspaceState.DIAGNOSTIC_HOLD)
        with pytest.raises(WorkspaceLifecycleError):
            lease.write_authorization_revoked()

    def test_revoke_twice_is_rejected(self) -> None:
        lease = _lease(state=WorkspaceState.WRITE_AUTHORIZATION_REVOKED)
        with pytest.raises(WorkspaceLifecycleError):
            lease.write_authorization_revoked()


class TestDestruction:
    @pytest.mark.parametrize(
        "state",
        [
            WorkspaceState.LEASED,
            WorkspaceState.DIAGNOSTIC_HOLD,
            WorkspaceState.WRITE_AUTHORIZATION_REVOKED,
        ],
    )
    def test_destroy_from_any_non_destroyed_state_succeeds(self, state: WorkspaceState) -> None:
        lease = _lease(state=state)
        destroyed = lease.destroyed()
        assert destroyed.state == WorkspaceState.DESTROYED

    def test_destroy_twice_is_rejected(self) -> None:
        lease = _lease(state=WorkspaceState.DESTROYED)
        with pytest.raises(WorkspaceLifecycleError, match="already destroyed"):
            lease.destroyed()


def test_blank_actor_rejected() -> None:
    with pytest.raises(WorkspaceLifecycleError):
        _lease(actor="  ")


def test_non_positive_max_lifetime_rejected() -> None:
    with pytest.raises(WorkspaceLifecycleError):
        _lease(max_lifetime_seconds=0)
