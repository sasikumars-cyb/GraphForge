"""add human_override to agent_steps

Adds a small, additive override sidecar directly on `agent_steps` rather
than a separate `resolved_contexts`-style table (see the Context Explorer
architecture review). `result` keeps meaning exactly what it always has —
what the agent produced, untouched — so confidence calibration
(app.models.confidence_calibration) keeps checking a real AI output
against the human approve/reject decision. `human_override` holds only
the fields a human actually changed; `get_stage_result()`
(app.agents.git_ops._artifact_reader) merges it on top of `result` at
read time. `overridden_by_user_id`/`overridden_at` are the audit trail.

Purely additive: all three columns nullable, no default needed since
"no override" is the overwhelmingly common state and NULL already means
exactly that — every existing row is valid with no backfill.

Revision ID: d8e9f0a1b2c3
Revises: b2c3d4e5f6a7
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d8e9f0a1b2c3"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain JSON, matching this table's existing `evidence`/`result`
    # columns (app.models.agent_step) — not JSONB, to stay consistent
    # within the same table rather than mixing JSON variants.
    op.add_column(
        "agent_steps",
        sa.Column("human_override", sa.JSON(), nullable=True),
    )
    op.add_column(
        "agent_steps",
        sa.Column("overridden_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_steps",
        sa.Column("overridden_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_steps_overridden_by_user_id",
        "agent_steps",
        "users",
        ["overridden_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_steps_overridden_by_user_id", "agent_steps", type_="foreignkey"
    )
    op.drop_column("agent_steps", "overridden_at")
    op.drop_column("agent_steps", "overridden_by_user_id")
    op.drop_column("agent_steps", "human_override")
