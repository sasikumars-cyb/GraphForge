"""add pull request ai analyses table

Revision ID: a1b2c3d4e5f6
Revises: 14341b7d313b
Create Date: 2026-07-22 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "14341b7d313b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pull_request_ai_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pull_request_id", sa.Uuid(), nullable=False),
        sa.Column("executive_summary", sa.Text(), nullable=False),
        sa.Column("breaking_changes", sa.JSON(), nullable=False),
        sa.Column("migration_advice", sa.JSON(), nullable=False),
        sa.Column("suggested_reviewers", sa.JSON(), nullable=False),
        sa.Column("regression_tests", sa.JSON(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("confidence_reasoning", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["pull_request_id"], ["pull_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pull_request_id"),
    )


def downgrade() -> None:
    op.drop_table("pull_request_ai_analyses")
