"""add approved_by_user_id to workflows

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-07-24 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workflows_approved_by_user_id",
        "workflows",
        "users",
        ["approved_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_workflows_approved_by_user_id", "workflows", type_="foreignkey")
    op.drop_column("workflows", "approved_by_user_id")
