"""extend engineering_events event_type for the Replan foundation
(Phase 6 — Plan supersession / PlanStep dependency / invalidation, ES §11)

Revision ID: 5e8f3a2b91c4
Revises: 7a3d9c1e5f28
Create Date: 2026-08-17 00:00:00.000000

Additive, matching `9d4f2a7c1e83`'s/`2b6f8e1a4c73`'s/`7a3d9c1e5f28`'s own
precedent exactly: each of those migrations is a point-in-time historical
record of the schema when it ran, never edited to add a later phase's
event types. This migration drops and recreates
`ck_engineering_events_event_type` with the Phase 1 + Phase 3 + Phase 4
vocabulary plus the ONE new Phase 6 event type
`app.engineering_state.events` now declares (`PlanStepInvalidated`) — the
minimum new type identified by the Phase 6 design audit as necessary to
durably mark an already-appended `PlanStepCreated` as invalidated without
editing it, justified against Engineering State contract §11
("Invalidation MUST propagate only to dependent PlanSteps in the DAG")
and §10 ("Contradiction... MUST trigger... dependent PlanStep
invalidation").

`PlanCreated.supersedes_plan_event_id` and `PlanStepCreated.depends_on`
are NOT new event types — they are additive, optional JSONB payload
fields on two ALREADY-allowed event types, requiring no schema change at
all (this is exactly why Phase 5 needed no migration for its own
optional-field additions to `PlanStepCreated`/`ObservationRecorded`).

Does not touch the append-only triggers, the unique constraint, or any
other part of the table — only the CHECK constraint's allowed value set.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e8f3a2b91c4"
down_revision: str | None = "7a3d9c1e5f28"
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

_PHASE_6_EVENT_TYPES = ("PlanStepInvalidated",)

_PRE_PHASE_6_EVENT_TYPES = _PHASE_1_EVENT_TYPES + _PHASE_3_EVENT_TYPES + _PHASE_4_EVENT_TYPES
_ALL_EVENT_TYPES = _PRE_PHASE_6_EVENT_TYPES + _PHASE_6_EVENT_TYPES


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
        "event_type IN (" + ", ".join(f"'{t}'" for t in sorted(_PRE_PHASE_6_EVENT_TYPES)) + ")",
    )
