"""`WorkspaceLifecycleService` — Cap §19's Workspace lifecycle, durably
recorded.

Constructed and owned exclusively by `ControlPlane` (see
`app.control_plane.control_plane`) — never imported or instantiated by
anything Reasoning-Plane-adjacent. This is an IMPLEMENTATION component,
not a second authority: every public method here is reached only through
`ControlPlane`'s own methods, enforced structurally by
`tests/unit/architecture/test_workspace_authority_boundary.py`.

    ControlPlane
        |
        `-- WorkspaceLifecycleService
                |-- Engineering State (durable truth)
                `-- physical workspace backend (reconciled toward it)

Durable state is always authoritative for whether a Workspace's lease
claim is valid; physical state is always eventually reconciled toward
whatever the durable state says, never the reverse (Phase 4 design's
explicit invariant, replacing "best effort" with a precise rule).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.control_plane.policy import WORKSPACE_CONCURRENCY_RESOURCE, PolicyStore
from app.control_plane.workspace_model import (
    DestructionReason,
    WorkspaceLease,
    WorkspaceLifecycleError,
    WorkspaceState,
    is_within_total_lifetime,
)
from app.control_plane.workspace_physical import (
    create_physical_workspace,
    destroy_physical_workspace,
    physical_location_for,
    physical_workspace_exists,
)
from app.engineering_state.events import (
    WORKSPACE_CREATED,
    WORKSPACE_DESTROYED,
    WORKSPACE_DIAGNOSTIC_HOLD_ENTERED,
    WORKSPACE_LEASE_RENEWED,
    WORKSPACE_WRITE_AUTHORIZATION_REVOKED,
)
from app.engineering_state.materialize import WorkspaceRecord, fold, is_hold_expired, is_reclaimable
from app.models.engineering_event import EngineeringEvent
from app.repositories.engineering_event_repository import EngineeringEventRepository

_SERVICE_ACTOR = "control_plane"  # EngineeringEvent.actor: the WRITER, always Control Plane.

_WORKSPACE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        WORKSPACE_CREATED,
        WORKSPACE_LEASE_RENEWED,
        WORKSPACE_DIAGNOSTIC_HOLD_ENTERED,
        WORKSPACE_WRITE_AUTHORIZATION_REVOKED,
        WORKSPACE_DESTROYED,
    }
)

_OPEN_STATES: frozenset[str] = frozenset(
    {
        WorkspaceState.LEASED.value,
        WorkspaceState.DIAGNOSTIC_HOLD.value,
        WorkspaceState.WRITE_AUTHORIZATION_REVOKED.value,
    }
)

# §19 disposition reasons a normal (non-custodial) destroy may cite.
# CUSTODIAL is deliberately excluded — reached only through
# `custodial_destroy_workspace`, never through `destroy_workspace`.
_NORMAL_DESTROY_REASONS: frozenset[DestructionReason] = frozenset(
    {
        DestructionReason.COMPLETED_SUCCESS,
        DestructionReason.DIAGNOSTIC_HOLD_EXPIRED,
        DestructionReason.LEASE_EXPIRED_RECLAIMED,
        DestructionReason.CREDENTIAL_INCIDENT,
        DestructionReason.CREATION_FAILED,
    }
)


class WorkspaceCapExceededError(RuntimeError):
    """Raised when creating a Workspace would exceed the Policy-governed
    concurrency cap (Cap §19: "Policy MUST cap concurrent Workspaces per
    Role and per tenant") or when no cap is configured at all (fail
    closed, per §20.3: "unreadable or unresolvable Policy = deny")."""


class WorkspaceNotFoundError(ValueError):
    """No `WorkspaceCreated` event exists for the given identity."""


class WorkspaceAuthorizationRevokedError(WorkspaceLifecycleError):
    """Raised by a non-custodial write operation against a Workspace
    whose write authorization has been durably revoked — Cap §19:
    "retained, no further writes." Checked fresh against durable state
    on every call, never cached."""


class WorkspaceCustodialOwnershipError(ValueError):
    """Raised when a custodial destroy is attempted by an
    (actor, user_id) pair that does not own the Workspace — Cap §18.3
    scopes custodial destruction to "a Role's own resources.\" """


class WorkspaceLifecycleService:
    def __init__(self, *, event_repository: EngineeringEventRepository, policy_store: PolicyStore):
        self._events = event_repository
        self._policy = policy_store

    # ------------------------------------------------------------------
    # Creation — Policy-capped, race-safe
    # ------------------------------------------------------------------

    async def create_workspace(
        self,
        *,
        task_id: uuid.UUID,
        actor: str,
        user_id: uuid.UUID,
        execution_context: dict[str, object],
        repository_url: str | None,
        ref: str = "main",
        access_token: str | None = None,
        initial_lease_seconds: int,
        max_lifetime_seconds: int,
    ) -> tuple[WorkspaceLease, uuid.UUID]:
        """Returns `(lease, created_event_id)`. Race-safe against
        concurrent creation at the cap boundary: a `pg_advisory_xact_lock`
        keyed on `(actor, user_id)` — NOT `task_id`, since the cap is a
        cross-task, per-(Role,tenant) count — is held across the count
        check AND the `WorkspaceCreated` append + commit, closing the
        TOCTOU race explicitly (mirrors Phase 3 correction #3's
        discipline: durable check inside the lock, not trusted from
        before it was acquired).
        """
        now = datetime.now(UTC)
        await self._acquire_owner_lock(actor=actor, user_id=user_id)

        limit = self._policy.resource_limit(WORKSPACE_CONCURRENCY_RESOURCE)
        if limit is None:
            raise WorkspaceCapExceededError(
                "No Workspace concurrency cap is configured in Policy at any scope — "
                "fail-closed deny (Cap §20.3: unreadable/unresolvable Policy = deny)."
            )

        current_count = await self._count_open_workspaces(actor=actor, user_id=user_id, now=now)
        if current_count >= limit:
            raise WorkspaceCapExceededError(
                f"Creating a Workspace for actor={actor!r} user_id={user_id} would exceed "
                f"the Policy-governed cap of {limit} (currently {current_count} open)."
            )

        workspace_id = uuid.uuid4()
        initial_expires_at = now + timedelta(seconds=initial_lease_seconds)

        created_event = await self._events.append(
            task_id=task_id,
            event_type=WORKSPACE_CREATED,
            payload={
                "workspace_id": str(workspace_id),
                "task_id": str(task_id),
                "actor": actor,
                "user_id": str(user_id),
                "execution_context": execution_context,
                # Deterministic from workspace_id (see
                # workspace_physical.physical_location_for), computed
                # before this append so the durable record and the
                # physical directory always agree by construction.
                "physical_location": physical_location_for(workspace_id),
                "repository_url": repository_url,
                "created_at": now.isoformat(),
                "max_lifetime_seconds": max_lifetime_seconds,
                "initial_expires_at": initial_expires_at.isoformat(),
            },
            actor=_SERVICE_ACTOR,
        )
        # Phase 3 correction #1's exact discipline, applied here: commit
        # BEFORE the physical (external, slow, failure-prone) operation,
        # so a crash during/after cloning leaves a durable, correctly
        # reclaimable record (Phase 4 design Case C) rather than losing
        # the fact that creation was attempted.
        await self._events._db.commit()

        try:
            physical_location = await create_physical_workspace(
                workspace_id=workspace_id,
                repository_url=repository_url,
                ref=ref,
                access_token=access_token,
            )
        except Exception as exc:
            # Case B: a synchronously-caught creation failure self-heals
            # within this same call — append+commit the closing
            # Destroyed event immediately, so durable state never claims
            # a Workspace exists that was never successfully created.
            await self._events.append(
                task_id=task_id,
                event_type=WORKSPACE_DESTROYED,
                payload={
                    "workspace_event_id": str(created_event.id),
                    "reason": DestructionReason.CREATION_FAILED.value,
                },
                actor=_SERVICE_ACTOR,
                causation_event_id=created_event.id,
            )
            await self._events._db.commit()
            raise WorkspaceLifecycleError(f"Workspace creation failed: {exc}") from exc

        lease = WorkspaceLease(
            workspace_id=workspace_id,
            task_id=task_id,
            actor=actor,
            user_id=user_id,
            execution_context=execution_context,
            physical_location=physical_location,
            repository_url=repository_url,
            created_at=now,
            max_lifetime_seconds=max_lifetime_seconds,
            lease_expires_at=initial_expires_at,
            renewal_count=0,
        )
        return lease, created_event.id

    async def _acquire_owner_lock(self, *, actor: str, user_id: uuid.UUID) -> None:
        await self._events._db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"),
            {"key": f"workspace-owner:{actor}:{user_id}"},
        )

    async def _acquire_workspace_lock(self, *, workspace_event_id: uuid.UUID) -> None:
        await self._events._db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"),
            {"key": f"workspace:{workspace_event_id}"},
        )

    async def _count_open_workspaces(self, *, actor: str, user_id: uuid.UUID, now: datetime) -> int:
        records = await self._all_workspace_records()
        count = 0
        for record in records:
            if record.actor != actor or record.user_id != user_id:
                continue
            if record.state not in _OPEN_STATES:
                continue
            if record.state == WorkspaceState.LEASED.value and is_reclaimable(record, now=now):
                # An expired-but-not-yet-destroyed Workspace does not
                # count against the cap — Phase 4 design's explicit
                # closing of the "lazy reclaim leaves the cap
                # permanently exhausted" gap.
                continue
            if record.state == WorkspaceState.DIAGNOSTIC_HOLD.value and is_hold_expired(
                record, now=now
            ):
                # Same closing, applied to the hold-TTL expiry mechanism
                # found during this phase's exit self-audit — a
                # hold-expired-but-not-yet-swept Workspace must not
                # permanently exhaust the cap either.
                continue
            count += 1
        return count

    async def _all_workspace_records(self) -> list[WorkspaceRecord]:
        """Cross-task: groups raw events by `task_id` (fold() itself
        requires a single task's stream) and folds each group
        independently, merging their `.workspaces`."""
        raw_events = await self._events.list_by_event_types(_WORKSPACE_EVENT_TYPES)
        grouped: dict[uuid.UUID, list[EngineeringEvent]] = defaultdict(list)
        for event in raw_events:
            grouped[event.task_id].append(event)
        records: list[WorkspaceRecord] = []
        for task_events in grouped.values():
            records.extend(fold(task_events).workspaces)
        return records

    async def _get_record(
        self, *, task_id: uuid.UUID, workspace_event_id: uuid.UUID
    ) -> WorkspaceRecord:
        events = await self._events.list_for_task(task_id)
        state = fold(events)
        for record in state.workspaces:
            if record.workspace_event_id == workspace_event_id:
                return record
        raise WorkspaceNotFoundError(
            f"No Workspace found for task_id={task_id} workspace_event_id={workspace_event_id}."
        )

    # ------------------------------------------------------------------
    # Renewal — leased -> leased only, E <= T0 + M
    # ------------------------------------------------------------------

    async def renew_workspace_lease(
        self, *, task_id: uuid.UUID, workspace_event_id: uuid.UUID, new_expires_at: datetime
    ) -> WorkspaceRecord:
        await self._acquire_workspace_lock(workspace_event_id=workspace_event_id)
        record = await self._get_record(task_id=task_id, workspace_event_id=workspace_event_id)
        self._require_write_authorized(record)
        if record.state != WorkspaceState.LEASED.value:
            raise WorkspaceLifecycleError(
                f"Workspace {workspace_event_id} cannot be renewed from state "
                f"{record.state!r}; only a leased Workspace may be renewed."
            )
        created_at = datetime.fromisoformat(record.created_at)
        if not is_within_total_lifetime(
            created_at=created_at,
            proposed_expiry=new_expires_at,
            max_lifetime_seconds=record.max_lifetime_seconds,
        ):
            raise WorkspaceLifecycleError(
                f"Workspace {workspace_event_id}: proposed expiry {new_expires_at} exceeds "
                f"the total-lifetime ceiling (created_at={created_at} + "
                f"max_lifetime_seconds={record.max_lifetime_seconds})."
            )
        new_renewal_count = record.renewal_count + 1
        await self._events.append(
            task_id=task_id,
            event_type=WORKSPACE_LEASE_RENEWED,
            payload={
                "workspace_event_id": str(workspace_event_id),
                "new_expires_at": new_expires_at.isoformat(),
                "renewal_count": new_renewal_count,
            },
            actor=_SERVICE_ACTOR,
            causation_event_id=workspace_event_id,
        )
        return await self._get_record(task_id=task_id, workspace_event_id=workspace_event_id)

    # ------------------------------------------------------------------
    # Diagnostic hold — leased -> diagnostic_hold
    # ------------------------------------------------------------------

    async def enter_diagnostic_hold(
        self,
        *,
        task_id: uuid.UUID,
        workspace_event_id: uuid.UUID,
        reason: str,
        hold_ttl_seconds: int,
    ) -> WorkspaceRecord:
        await self._acquire_workspace_lock(workspace_event_id=workspace_event_id)
        record = await self._get_record(task_id=task_id, workspace_event_id=workspace_event_id)
        self._require_write_authorized(record)
        if record.state != WorkspaceState.LEASED.value:
            raise WorkspaceLifecycleError(
                f"Workspace {workspace_event_id} cannot enter diagnostic hold from state "
                f"{record.state!r}."
            )
        hold_expires_at = datetime.now(UTC) + timedelta(seconds=hold_ttl_seconds)
        await self._events.append(
            task_id=task_id,
            event_type=WORKSPACE_DIAGNOSTIC_HOLD_ENTERED,
            payload={
                "workspace_event_id": str(workspace_event_id),
                "reason": reason,
                "hold_expires_at": hold_expires_at.isoformat(),
            },
            actor=_SERVICE_ACTOR,
            causation_event_id=workspace_event_id,
        )
        return await self._get_record(task_id=task_id, workspace_event_id=workspace_event_id)

    # ------------------------------------------------------------------
    # Authorization loss — leased -> write_authorization_revoked
    # ------------------------------------------------------------------

    async def revoke_write_authorization(
        self, *, task_id: uuid.UUID, workspace_event_id: uuid.UUID, reason: str
    ) -> WorkspaceRecord:
        await self._acquire_workspace_lock(workspace_event_id=workspace_event_id)
        record = await self._get_record(task_id=task_id, workspace_event_id=workspace_event_id)
        if record.state != WorkspaceState.LEASED.value:
            raise WorkspaceLifecycleError(
                f"Workspace {workspace_event_id} cannot have write authorization revoked "
                f"from state {record.state!r}."
            )
        await self._events.append(
            task_id=task_id,
            event_type=WORKSPACE_WRITE_AUTHORIZATION_REVOKED,
            payload={"workspace_event_id": str(workspace_event_id), "reason": reason},
            actor=_SERVICE_ACTOR,
            causation_event_id=workspace_event_id,
        )
        return await self._get_record(task_id=task_id, workspace_event_id=workspace_event_id)

    def _require_write_authorized(self, record: WorkspaceRecord) -> None:
        """Cap §19's "no further writes" guarantee, structurally enforced
        — a fresh, durable, re-checked-every-call gate, never a
        documentation-only promise. The ONLY exempt path is custodial
        destruction (§18.3), which never calls this."""
        if record.state == WorkspaceState.WRITE_AUTHORIZATION_REVOKED.value:
            raise WorkspaceAuthorizationRevokedError(
                f"Workspace {record.workspace_event_id} has had its write authorization "
                "revoked — no further writes permitted (custodial destruction remains "
                "available)."
            )

    # ------------------------------------------------------------------
    # Destruction — normal (reason-gated) and custodial (ownership-only)
    # ------------------------------------------------------------------

    async def destroy_workspace(
        self, *, task_id: uuid.UUID, workspace_event_id: uuid.UUID, reason: DestructionReason
    ) -> WorkspaceRecord:
        if reason not in _NORMAL_DESTROY_REASONS:
            raise WorkspaceLifecycleError(
                f"{reason.value!r} is not a valid reason for a normal (non-custodial) "
                "destroy — use custodial_destroy_workspace for CUSTODIAL."
            )
        await self._acquire_workspace_lock(workspace_event_id=workspace_event_id)
        record = await self._get_record(task_id=task_id, workspace_event_id=workspace_event_id)
        if record.state == WorkspaceState.DESTROYED.value:
            raise WorkspaceLifecycleError(f"Workspace {workspace_event_id} is already destroyed.")
        # §19: "Diagnostic hold yields to data classification" — a
        # credential incident destroys immediately from ANY state,
        # overriding hold. Every other normal-destroy reason requires a
        # write-authorized Workspace (write-revoked Workspaces are
        # closed only via custodial destroy, per §18.3's own rationale
        # for existing).
        if (
            reason != DestructionReason.CREDENTIAL_INCIDENT
            and record.state == WorkspaceState.WRITE_AUTHORIZATION_REVOKED.value
        ):
            raise WorkspaceAuthorizationRevokedError(
                f"Workspace {workspace_event_id} has had its write authorization revoked; "
                f"only custodial destruction or a credential incident may close it now."
            )
        return await self._append_destroyed(
            task_id=task_id, workspace_event_id=workspace_event_id, reason=reason
        )

    async def custodial_destroy_workspace(
        self, *, task_id: uuid.UUID, workspace_event_id: uuid.UUID, actor: str, user_id: uuid.UUID
    ) -> WorkspaceRecord:
        """Cap §18.3: "destroy one's own Workspace... MUST always keep
        authorizable for a Role's own resources, at tenant scope,
        independent of task state." Deliberately does NOT call
        `_require_write_authorized` — that check is exactly what this
        path exists to bypass. Ownership is the ONLY gate: the caller's
        `(actor, user_id)` must match the Workspace's own recorded
        owner, checked by equality against durable state, never against
        the owning task's current Grant/Policy/authorization status.
        """
        await self._acquire_workspace_lock(workspace_event_id=workspace_event_id)
        record = await self._get_record(task_id=task_id, workspace_event_id=workspace_event_id)
        if record.actor != actor or record.user_id != user_id:
            raise WorkspaceCustodialOwnershipError(
                f"actor={actor!r} user_id={user_id} does not own Workspace "
                f"{workspace_event_id} (owned by actor={record.actor!r} "
                f"user_id={record.user_id})."
            )
        if record.state == WorkspaceState.DESTROYED.value:
            raise WorkspaceLifecycleError(f"Workspace {workspace_event_id} is already destroyed.")
        return await self._append_destroyed(
            task_id=task_id,
            workspace_event_id=workspace_event_id,
            reason=DestructionReason.CUSTODIAL,
        )

    async def _append_destroyed(
        self, *, task_id: uuid.UUID, workspace_event_id: uuid.UUID, reason: DestructionReason
    ) -> WorkspaceRecord:
        await self._events.append(
            task_id=task_id,
            event_type=WORKSPACE_DESTROYED,
            payload={"workspace_event_id": str(workspace_event_id), "reason": reason.value},
            actor=_SERVICE_ACTOR,
            causation_event_id=workspace_event_id,
        )
        await self._events._db.commit()
        record = await self._get_record(task_id=task_id, workspace_event_id=workspace_event_id)
        # Physical cleanup happens AFTER the durable Destroyed commit —
        # Phase 4 design's exact ordering: durable truth first, physical
        # reality reconciled toward it, never the reverse. Best-effort;
        # a failure here is closed later by the sweep (Case D).
        destroy_physical_workspace(record.physical_location)
        return record

    # ------------------------------------------------------------------
    # Sweep — crash/orphan reconciliation (Cap §19: "Reclaimed by
    # lease-TTL expiry via an orphan sweep")
    # ------------------------------------------------------------------

    async def run_sweep(self, *, now: datetime | None = None) -> dict[str, int]:
        """One reconciliation pass, handling every case Phase 4's design
        (and its own exit self-audit) identified as belonging to this
        single mechanism:

          1. leased Workspace whose lease TTL has expired with no
             Destroyed event yet -> reclaim (append Destroyed, commit,
             then attempt physical cleanup);
          2. Workspace under diagnostic hold whose hold's OWN TTL has
             expired -> destroy (§19: "retained under diagnostic hold
             with bounded TTL, then destroyed" — a distinct expiry
             mechanism from case 1, found missing during this phase's
             own exit audit and closed here rather than left silently
             unhandled);
          3. Destroyed Workspace whose physical location still exists
             because a prior cleanup attempt failed -> retry physical
             cleanup only (no new event needed).

        Idempotent and safe under concurrent invocation: every mutating
        action re-validates durable state fresh, inside the SAME
        per-Workspace advisory lock the normal lifecycle operations use,
        immediately before acting — running this against an
        already-reconciled Workspace is a no-op at every step.
        """
        current_time = now if now is not None else datetime.now(UTC)
        records = await self._all_workspace_records()
        reclaimed = 0
        physically_cleaned = 0
        already_clean = 0
        still_failing = 0

        for record in records:
            if record.state == WorkspaceState.LEASED.value and is_reclaimable(
                record, now=current_time
            ):
                await self._acquire_workspace_lock(workspace_event_id=record.workspace_event_id)
                fresh = await self._get_record(
                    task_id=record.task_id, workspace_event_id=record.workspace_event_id
                )
                if fresh.state == WorkspaceState.LEASED.value and is_reclaimable(
                    fresh, now=current_time
                ):
                    await self._append_destroyed(
                        task_id=fresh.task_id,
                        workspace_event_id=fresh.workspace_event_id,
                        reason=DestructionReason.LEASE_EXPIRED_RECLAIMED,
                    )
                    reclaimed += 1
            elif record.state == WorkspaceState.DIAGNOSTIC_HOLD.value and is_hold_expired(
                record, now=current_time
            ):
                await self._acquire_workspace_lock(workspace_event_id=record.workspace_event_id)
                fresh = await self._get_record(
                    task_id=record.task_id, workspace_event_id=record.workspace_event_id
                )
                if fresh.state == WorkspaceState.DIAGNOSTIC_HOLD.value and is_hold_expired(
                    fresh, now=current_time
                ):
                    await self._append_destroyed(
                        task_id=fresh.task_id,
                        workspace_event_id=fresh.workspace_event_id,
                        reason=DestructionReason.DIAGNOSTIC_HOLD_EXPIRED,
                    )
                    reclaimed += 1
            elif record.state == WorkspaceState.DESTROYED.value and physical_workspace_exists(
                record.physical_location
            ):
                await self._acquire_workspace_lock(workspace_event_id=record.workspace_event_id)
                fresh = await self._get_record(
                    task_id=record.task_id, workspace_event_id=record.workspace_event_id
                )
                if fresh.state == WorkspaceState.DESTROYED.value and physical_workspace_exists(
                    fresh.physical_location
                ):
                    cleaned = destroy_physical_workspace(fresh.physical_location)
                    if cleaned:
                        physically_cleaned += 1
                    else:
                        still_failing += 1
                else:
                    already_clean += 1

        return {
            "reclaimed": reclaimed,
            "physically_cleaned": physically_cleaned,
            "already_clean": already_clean,
            "still_failing": still_failing,
        }


__all__ = [
    "WorkspaceAuthorizationRevokedError",
    "WorkspaceCapExceededError",
    "WorkspaceCustodialOwnershipError",
    "WorkspaceLifecycleService",
    "WorkspaceNotFoundError",
]
