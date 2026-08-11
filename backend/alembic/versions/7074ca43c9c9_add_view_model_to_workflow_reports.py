"""add view_model to workflow_reports

Revision ID: 7074ca43c9c9
Revises: f3a1c9e0b2d4
Create Date: 2026-08-09 09:57:04.818823

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7074ca43c9c9"
down_revision: str | None = "f3a1c9e0b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_reports",
        sa.Column("view_model", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_reports", "view_model")
