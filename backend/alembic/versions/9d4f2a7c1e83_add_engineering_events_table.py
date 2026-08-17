"""add engineering_events table (Phase 1 — Engineering State foundation)

Revision ID: 9d4f2a7c1e83
Revises: 7c2a1f9d4e6b
Create Date: 2026-08-17 00:00:00.000000

Creates the append-only event log
`docs/graphforge/ENGINEERING_STATE_ARCHITECTURE.md` requires as the sole
source of authoritative truth for a task's reasoning trace (§8, Final
Contract §11). Two enforcement layers, deliberately redundant:

1. `(task_id, sequence_number)` UNIQUE — the concurrency backstop behind
   `EngineeringEventRepository.append`'s advisory-lock serialization.
2. `event_type` CHECK constraint — the closed Phase 1 vocabulary
   (`app.engineering_state.events.EVENT_TYPES`), hardcoded here as a
   literal list rather than imported from `app.*`: a migration is a
   point-in-time historical record of what the schema looked like when
   it ran, not living code that should track a Python module that may
   itself change in a later migration.

Plus the append-only guarantee itself: `BEFORE UPDATE OR DELETE` triggers
that raise on any attempt, closing the final adversarial sequencing
review's explicit guardrail ("DB-permission-level enforcement... on the
events table") without requiring a separate restricted database role
(which would need knowing, and never accidentally breaking, whichever
role every other table's migrations already assume connects as — a
trigger is self-contained and works regardless of connecting role,
correctly the smaller, safer mechanism for this phase).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d4f2a7c1e83"
down_revision: str | None = "7c2a1f9d4e6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The Phase 1 event vocabulary, exactly as declared in
# app.engineering_state.events.EVENT_TYPES at the time this migration was
# written. Extending it is an additive migration (a later phase adds a
# new event class here as its own change), never an edit of this file.
_EVENT_TYPES = (
    "GoalCreated",
    "GoalUpdated",
    "PlanCreated",
    "PlanStepCreated",
    "DecisionMade",
    "EvidenceRecorded",
    "BeliefRecorded",
    "ObservationRecorded",
)


def upgrade() -> None:
    op.create_table(
        "engineering_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("causation_event_id", sa.Uuid(), nullable=True),
        sa.Column("execution_context", postgresql.JSONB(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["causation_event_id"], ["engineering_events.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "task_id", "sequence_number", name="uq_engineering_events_task_sequence"
        ),
        sa.CheckConstraint(
            "event_type IN (" + ", ".join(f"'{t}'" for t in sorted(_EVENT_TYPES)) + ")",
            name="ck_engineering_events_event_type",
        ),
        sa.CheckConstraint("sequence_number > 0", name="ck_engineering_events_sequence_positive"),
    )
    op.create_index("ix_engineering_events_task_id", "engineering_events", ["task_id"])

    op.execute("""
        CREATE OR REPLACE FUNCTION engineering_events_append_only()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'engineering_events is append-only: % is not permitted (id=%)',
                TG_OP, OLD.id
                USING ERRCODE = 'raise_exception';
        END;
        $$ LANGUAGE plpgsql;
        """)
    op.execute("""
        CREATE TRIGGER engineering_events_forbid_update
        BEFORE UPDATE ON engineering_events
        FOR EACH ROW EXECUTE FUNCTION engineering_events_append_only();
        """)
    op.execute("""
        CREATE TRIGGER engineering_events_forbid_delete
        BEFORE DELETE ON engineering_events
        FOR EACH ROW EXECUTE FUNCTION engineering_events_append_only();
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS engineering_events_forbid_delete ON engineering_events")
    op.execute("DROP TRIGGER IF EXISTS engineering_events_forbid_update ON engineering_events")
    op.execute("DROP FUNCTION IF EXISTS engineering_events_append_only()")
    op.drop_index("ix_engineering_events_task_id", table_name="engineering_events")
    op.drop_table("engineering_events")
