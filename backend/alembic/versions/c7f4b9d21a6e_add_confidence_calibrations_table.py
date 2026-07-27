"""add confidence_calibrations table

Revision ID: c7f4b9d21a6e
Revises: deb3a56c571b
Create Date: 2026-07-27 00:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7f4b9d21a6e"
down_revision: str | None = "deb3a56c571b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "confidence_calibrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_confidence_calibrations_workflow_id", "confidence_calibrations", ["workflow_id"]
    )
    op.create_index(
        "ix_confidence_calibrations_agent_id", "confidence_calibrations", ["agent_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_confidence_calibrations_agent_id", table_name="confidence_calibrations")
    op.drop_index("ix_confidence_calibrations_workflow_id", table_name="confidence_calibrations")
    op.drop_table("confidence_calibrations")
