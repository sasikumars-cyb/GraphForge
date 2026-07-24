"""add workflow_type and source_workflow_id to workflows

Revision ID: b4c5d6e7f8a9
Revises: a2b3c4d5e6f7
Create Date: 2026-07-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "workflow_type",
            sa.String(length=32),
            nullable=False,
            server_default="legacy_sdlc",
        ),
    )
    op.add_column(
        "workflows",
        sa.Column("source_workflow_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workflows_source_workflow_id",
        "workflows",
        "workflows",
        ["source_workflow_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_workflows_source_workflow_id", "workflows", type_="foreignkey")
    op.drop_column("workflows", "source_workflow_id")
    op.drop_column("workflows", "workflow_type")
