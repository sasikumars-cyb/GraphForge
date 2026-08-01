"""add workflow_reports table

One row per generated high-level HTML report — created when a Planning
workflow's blueprint is approved, filled in by the report_generation
agent running in the background. See app.models.workflow_report.

Revision ID: ba8b3fb42d8a
Revises: 42c15cf49ec2
"""

import sqlalchemy as sa

from alembic import op

revision = "ba8b3fb42d8a"
down_revision = "42c15cf49ec2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("html_content", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_workflow_reports_workflow_id"), "workflow_reports", ["workflow_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_workflow_reports_workflow_id"), table_name="workflow_reports")
    op.drop_table("workflow_reports")
