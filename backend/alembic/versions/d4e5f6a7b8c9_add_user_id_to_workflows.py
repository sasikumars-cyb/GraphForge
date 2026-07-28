"""add user_id to workflows

Revision ID: d4e5f6a7b8c9
Revises: c7f4b9d21a6e
Create Date: 2026-07-27 17:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c7f4b9d21a6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workflows_user_id_users",
        "workflows",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_workflows_user_id", "workflows", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_workflows_user_id", table_name="workflows")
    op.drop_constraint("fk_workflows_user_id_users", "workflows", type_="foreignkey")
    op.drop_column("workflows", "user_id")
