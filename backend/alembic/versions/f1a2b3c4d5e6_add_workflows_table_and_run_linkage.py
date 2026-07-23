"""add workflows table and run linkage columns

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-07-23 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=False, server_default="planning"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_progress"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add workflow linkage columns to agent_runs
    op.add_column(
        "agent_runs",
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("workflow_stage", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("previous_run_id", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_agent_runs_workflow_id", "agent_runs", ["workflow_id"])
    op.create_foreign_key(
        "fk_agent_runs_workflow_id",
        "agent_runs",
        "workflows",
        ["workflow_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_agent_runs_previous_run_id",
        "agent_runs",
        "agent_runs",
        ["previous_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_runs_previous_run_id", "agent_runs", type_="foreignkey")
    op.drop_constraint("fk_agent_runs_workflow_id", "agent_runs", type_="foreignkey")
    op.drop_index("ix_agent_runs_workflow_id", table_name="agent_runs")
    op.drop_column("agent_runs", "previous_run_id")
    op.drop_column("agent_runs", "workflow_stage")
    op.drop_column("agent_runs", "workflow_id")
    op.drop_table("workflows")
