"""add pull request analyses table

Revision ID: 14341b7d313b
Revises: b873164220d3
Create Date: 2026-07-22 07:36:14.847113

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "14341b7d313b"
down_revision: str | None = "b873164220d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pull_request_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pull_request_id", sa.Uuid(), nullable=False),
        sa.Column("risk", sa.String(length=10), nullable=False),
        sa.Column("directly_impacted_services", sa.JSON(), nullable=False),
        sa.Column("indirectly_impacted_services", sa.JSON(), nullable=False),
        sa.Column("impacted_apis", sa.JSON(), nullable=False),
        sa.Column("impacted_topics", sa.JSON(), nullable=False),
        sa.Column("impacted_libraries", sa.JSON(), nullable=False),
        sa.Column("dependency_paths", sa.JSON(), nullable=False),
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
    op.drop_table("pull_request_analyses")
