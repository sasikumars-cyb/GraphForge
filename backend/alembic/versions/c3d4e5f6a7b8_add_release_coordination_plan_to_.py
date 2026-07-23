"""add release_coordination_plan to pull_request_ai_analyses

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-07-23 06:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("release_coordination_plan", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pull_request_ai_analyses", "release_coordination_plan")
