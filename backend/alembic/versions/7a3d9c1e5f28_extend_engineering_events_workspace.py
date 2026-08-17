"""extend engineering_events event_type for Workspace lifecycle
(Phase 4 — Workspace Lifecycle, Cap §19)

Revision ID: 7a3d9c1e5f28
Revises: 2b6f8e1a4c73
Create Date: 2026-08-17 00:00:00.000000

Additive, matching `9d4f2a7c1e83`'s and `2b6f8e1a4c73`'s own precedent:
each of those migrations is a point-in-time historical record of the
schema when it ran, never edited to add a later phase's event types.
This migration drops and recreates `ck_engineering_events_event_type`
with the Phase 1 + Phase 3 vocabulary plus the five Workspace lifecycle
event types `app.engineering_state.events` now declares
(`WorkspaceCreated`/`WorkspaceLeaseRenewed`/
`WorkspaceDiagnosticHoldEntered`/`WorkspaceWriteAuthorizationRevoked`/
`WorkspaceDestroyed`), justified against Capabilities contract §19 — see
`app/engineering_state/events.py`'s own comments for the per-event
justification (each represents a distinct durable fact §19 requires;
credential-incident/custodial/lease-expiry-reclaimed/success/hold-expiry
destruction dispositions are `WorkspaceDestroyed.reason` values, not
separate event types, mirroring the `AuthorizationDenied.denial_stage`
precedent).

Does not touch the append-only triggers, the unique constraint, or any
other part of the table — only the CHECK constraint's allowed value set.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a3d9c1e5f28"
down_revision: str | None = "2b6f8e1a4c73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PHASE_1_EVENT_TYPES = (
    "GoalCreated",
    "GoalUpdated",
    "PlanCreated",
    "PlanStepCreated",
    "DecisionMade",
    "EvidenceRecorded",
    "BeliefRecorded",
    "ObservationRecorded",
)

_PHASE_3_EVENT_TYPES = (
    "AuthorizationGranted",
    "AuthorizationConsuming",
    "AuthorizationConsumed",
    "AuthorizationDenied",
    "AuthorizationInvalidated",
)

_PHASE_4_EVENT_TYPES = (
    "WorkspaceCreated",
    "WorkspaceLeaseRenewed",
    "WorkspaceDiagnosticHoldEntered",
    "WorkspaceWriteAuthorizationRevoked",
    "WorkspaceDestroyed",
)

_PRE_PHASE_4_EVENT_TYPES = _PHASE_1_EVENT_TYPES + _PHASE_3_EVENT_TYPES
_ALL_EVENT_TYPES = _PRE_PHASE_4_EVENT_TYPES + _PHASE_4_EVENT_TYPES


def upgrade() -> None:
    op.drop_constraint("ck_engineering_events_event_type", "engineering_events", type_="check")
    op.create_check_constraint(
        "ck_engineering_events_event_type",
        "engineering_events",
        "event_type IN (" + ", ".join(f"'{t}'" for t in sorted(_ALL_EVENT_TYPES)) + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_engineering_events_event_type", "engineering_events", type_="check")
    op.create_check_constraint(
        "ck_engineering_events_event_type",
        "engineering_events",
        "event_type IN (" + ", ".join(f"'{t}'" for t in sorted(_PRE_PHASE_4_EVENT_TYPES)) + ")",
    )
