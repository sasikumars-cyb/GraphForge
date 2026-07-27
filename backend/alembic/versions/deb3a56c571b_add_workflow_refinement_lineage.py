"""add parent_workflow_id/version/refinement_note to workflows

Revision ID: deb3a56c571b
Revises: f1e2a3b4c5d6
Create Date: 2026-07-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "deb3a56c571b"
down_revision: str | None = "f1e2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("parent_workflow_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "workflows",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "workflows",
        sa.Column("refinement_note", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workflows_parent_workflow_id",
        "workflows",
        "workflows",
        ["parent_workflow_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_workflows_parent_workflow_id", "workflows", type_="foreignkey")
    op.drop_column("workflows", "refinement_note")
    op.drop_column("workflows", "version")
    op.drop_column("workflows", "parent_workflow_id")
