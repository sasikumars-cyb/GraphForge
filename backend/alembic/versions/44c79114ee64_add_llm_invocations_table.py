"""add llm_invocations table

Revision ID: 44c79114ee64
Revises: d8e9f0a1b2c3
Create Date: 2026-07-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "44c79114ee64"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_invocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_step_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False, server_default="initial"),
        sa.Column("sequence", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("finish_reason", sa.String(length=32), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempted_providers", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_step_id"], ["agent_steps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_invocations_agent_step_id", "llm_invocations", ["agent_step_id"])
    op.create_index("ix_llm_invocations_run_id", "llm_invocations", ["run_id"])
    # (run_id, started_at) — ADR 0012 Scalability: the dominant query shape
    # is "this run's invocation timeline, in order."
    op.create_index(
        "ix_llm_invocations_run_id_started_at", "llm_invocations", ["run_id", "started_at"]
    )
    # (provider, started_at) — provider comparison over time.
    op.create_index(
        "ix_llm_invocations_provider_started_at", "llm_invocations", ["provider", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_llm_invocations_provider_started_at", table_name="llm_invocations")
    op.drop_index("ix_llm_invocations_run_id_started_at", table_name="llm_invocations")
    op.drop_index("ix_llm_invocations_run_id", table_name="llm_invocations")
    op.drop_index("ix_llm_invocations_agent_step_id", table_name="llm_invocations")
    op.drop_table("llm_invocations")
