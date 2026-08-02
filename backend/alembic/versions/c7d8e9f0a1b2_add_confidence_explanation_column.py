"""add confidence explanation column to knowledge_relationships

Revision ID: c7d8e9f0a1b2
Revises: 09a3fe03cce9
Create Date: 2026-08-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "09a3fe03cce9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_relationships",
        sa.Column("explanation", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_relationships", "explanation")
