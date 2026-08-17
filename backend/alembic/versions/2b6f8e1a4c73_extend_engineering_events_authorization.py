"""extend engineering_events event_type for Authorization Grant lifecycle
(Phase 3 — Control Plane + ActionProposal + Authorization Grants)

Revision ID: 2b6f8e1a4c73
Revises: 9d4f2a7c1e83
Create Date: 2026-08-17 00:00:00.000000

Additive, as `9d4f2a7c1e83`'s own docstring requires: that migration is a
point-in-time historical record of the schema when it ran, never edited
to add a later phase's event types. This migration drops and recreates
`ck_engineering_events_event_type` with the Phase 1 vocabulary plus the
five Authorization Grant lifecycle event types
`app.engineering_state.events` now declares
(`AuthorizationGranted`/`AuthorizationConsuming`/`AuthorizationConsumed`/
`AuthorizationDenied`/`AuthorizationInvalidated`), justified against
Capabilities contract §7.1 (the durable Engineering State event classes
an Authorization Grant's full lifecycle requires) and the crash-safety
requirement (§11) for the extra "Consuming" state between "Granted" and
"Consumed" — see `app/engineering_state/events.py`'s module docstring for
the full justification.

Does not touch the append-only triggers, the unique constraint, or any
other part of the table — only the CHECK constraint's allowed value set.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2b6f8e1a4c73"
down_revision: str | None = "9d4f2a7c1e83"
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

_ALL_EVENT_TYPES = _PHASE_1_EVENT_TYPES + _PHASE_3_EVENT_TYPES


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
        "event_type IN (" + ", ".join(f"'{t}'" for t in sorted(_PHASE_1_EVENT_TYPES)) + ")",
    )
