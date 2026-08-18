"""Workspace — Cap §19.

In-memory shape and lifecycle transitions for a Workspace lease. Mirrors
`app.control_plane.grant`'s conventions (frozen dataclass, transition
methods returning a NEW instance, no database access here — persistence
is `WorkspaceLifecycleService`'s job) WITHOUT reusing Grant's semantics:
Workspace is explicitly not a Capability and is not part of the
ActionProposal -> Grant -> ToolExecutor pipeline (Cap §1). The states,
transitions, and invariants below are derived directly from §19's own
text, not analogized from `AuthorizationGrant`.

State machine (contract-native terms only — "leased" is grounded in
§19's own "a Workspace has... a bounded, renewable lease"; "diagnostic
hold" and "destroyed" are directly named; "write-authorization-revoked"
is NOT a contract term — the contract only describes its effect
("retained, no further writes"), and this module says so explicitly
rather than presenting an invented label as normative vocabulary):

    does-not-exist
          |  create
          v
        leased --renew (E<=T0+M)--> leased
          |
          |-(failure/halt)---------> diagnostic_hold -(hold TTL)-> destroyed
          |-(success)--------------------------------------------> destroyed
          |-(lease expires, no hold)------------------------------> destroyed
          |-(write-authorization revoked)-> write_authorization_revoked -(custodial)-> destroyed
          `-(credential/secret found, any state)---------------------------------> destroyed
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class WorkspaceLifecycleError(ValueError):
    """Raised on an attempted transition §19 does not permit from the
    lease's current state."""


class WorkspaceState(StrEnum):
    LEASED = "leased"
    DIAGNOSTIC_HOLD = "diagnostic_hold"
    WRITE_AUTHORIZATION_REVOKED = "write_authorization_revoked"
    DESTROYED = "destroyed"


class DestructionReason(StrEnum):
    """One event type (`WorkspaceDestroyed`) with a discriminating field
    — mirrors the precedent `AuthorizationDenied.denial_stage` already
    set in Phase 3 — rather than five separate destruction event types."""

    COMPLETED_SUCCESS = "completed_success"
    DIAGNOSTIC_HOLD_EXPIRED = "diagnostic_hold_expired"
    LEASE_EXPIRED_RECLAIMED = "lease_expired_reclaimed"
    CUSTODIAL = "custodial"
    CREDENTIAL_INCIDENT = "credential_incident"
    CREATION_FAILED = "creation_failed"


def is_within_total_lifetime(
    *, created_at: datetime, proposed_expiry: datetime, max_lifetime_seconds: int
) -> bool:
    """§19: "A Role MUST NOT be able to keep a Workspace alive
    indefinitely through lease renewal. Total lease lifetime is
    bounded." The exact invariant, anchored at creation time (T0), never
    at the previous expiry — renewals may only fill in time up to a
    ceiling fixed at birth, never push the ceiling itself outward:

        E <= T0 + M

    This is the fully-specified half of §19's renewal sentence. The
    other half — "renewals consume budget" — is deliberately NOT
    implemented here or anywhere in Phase 4: no Budget/Ledger model
    exists anywhere in this codebase, and the contract never defines
    budget's units, replenishment, or accounting (verified by exhaustive
    grep across all three contracts during Phase 4 design). Renewal
    count is tracked on `WorkspaceLease` for diagnostics only.
    """
    from datetime import timedelta

    return proposed_expiry <= created_at + timedelta(seconds=max_lifetime_seconds)


@dataclass(frozen=True, slots=True)
class WorkspaceLease:
    """A Workspace's current lease state. Frozen — every transition
    below returns a NEW instance; the object as last observed remains
    inspectable. `workspace_id` is a business identifier chosen at
    creation (distinct from the `WorkspaceCreated` event's own database
    id, exactly as `AuthorizationGrant.grant_id` is distinct from
    `granted_event_id` in Phase 3 — the service layer tracks the causal
    event id separately, this dataclass never does)."""

    workspace_id: uuid.UUID
    task_id: uuid.UUID
    actor: str
    user_id: uuid.UUID
    execution_context: dict[str, Any]
    physical_location: str
    repository_url: str | None
    created_at: datetime
    max_lifetime_seconds: int
    lease_expires_at: datetime
    renewal_count: int
    state: WorkspaceState = WorkspaceState.LEASED
    diagnostic_hold_expires_at: datetime | None = None
    diagnostic_hold_reason: str | None = None

    def __post_init__(self) -> None:
        if self.max_lifetime_seconds <= 0:
            raise WorkspaceLifecycleError("max_lifetime_seconds must be positive.")
        if not self.actor.strip():
            raise WorkspaceLifecycleError("WorkspaceLease.actor must be non-empty.")

    def is_lease_expired(self, *, now: datetime) -> bool:
        return now >= self.lease_expires_at

    def is_hold_expired(self, *, now: datetime) -> bool:
        if self.diagnostic_hold_expires_at is None:
            return False
        return now >= self.diagnostic_hold_expires_at

    def renewed(self, *, new_expires_at: datetime) -> WorkspaceLease:
        """`leased -> leased`, only. §19 says nothing about renewing a
        held, write-revoked, or destroyed Workspace — none of those are
        "the lease," they are dispositions that supersede it."""
        if self.state != WorkspaceState.LEASED:
            raise WorkspaceLifecycleError(
                f"Workspace {self.workspace_id} cannot be renewed from state "
                f"{self.state.value!r}; only a leased Workspace may be renewed."
            )
        if not is_within_total_lifetime(
            created_at=self.created_at,
            proposed_expiry=new_expires_at,
            max_lifetime_seconds=self.max_lifetime_seconds,
        ):
            raise WorkspaceLifecycleError(
                f"Workspace {self.workspace_id}: proposed expiry {new_expires_at} "
                f"exceeds the total-lifetime ceiling (created_at="
                f"{self.created_at} + max_lifetime_seconds="
                f"{self.max_lifetime_seconds})."
            )
        return _replace(
            self,
            lease_expires_at=new_expires_at,
            renewal_count=self.renewal_count + 1,
        )

    def entered_diagnostic_hold(self, *, reason: str, hold_expires_at: datetime) -> WorkspaceLease:
        """`leased -> diagnostic_hold`. §19: entered on failure/halt."""
        if self.state != WorkspaceState.LEASED:
            raise WorkspaceLifecycleError(
                f"Workspace {self.workspace_id} cannot enter diagnostic hold from "
                f"state {self.state.value!r}."
            )
        return _replace(
            self,
            state=WorkspaceState.DIAGNOSTIC_HOLD,
            diagnostic_hold_reason=reason,
            diagnostic_hold_expires_at=hold_expires_at,
        )

    def write_authorization_revoked(self) -> WorkspaceLease:
        """`leased -> write_authorization_revoked`. §19: "retained, no
        further writes, custodial destruction available." Only from
        `leased` — a Workspace already held or destroyed has no further
        "writes" to prohibit in the sense this transition means."""
        if self.state != WorkspaceState.LEASED:
            raise WorkspaceLifecycleError(
                f"Workspace {self.workspace_id} cannot have write authorization "
                f"revoked from state {self.state.value!r}."
            )
        return _replace(self, state=WorkspaceState.WRITE_AUTHORIZATION_REVOKED)

    def destroyed(self) -> WorkspaceLease:
        """Terminal, from any non-destroyed state — every disposition in
        §19's table (success, hold-expiry, lease-expiry, custodial,
        credential-incident) ends here."""
        if self.state == WorkspaceState.DESTROYED:
            raise WorkspaceLifecycleError(f"Workspace {self.workspace_id} is already destroyed.")
        return _replace(self, state=WorkspaceState.DESTROYED)


def _replace(lease: WorkspaceLease, **overrides: Any) -> WorkspaceLease:
    return dataclasses.replace(lease, **overrides)


__all__ = [
    "DestructionReason",
    "WorkspaceLease",
    "WorkspaceLifecycleError",
    "WorkspaceState",
    "is_within_total_lifetime",
]
