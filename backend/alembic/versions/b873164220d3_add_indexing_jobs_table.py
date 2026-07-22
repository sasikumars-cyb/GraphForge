"""add indexing jobs table

Revision ID: b873164220d3
Revises: 9b6b53ec45b8
Create Date: 2026-07-22 06:59:07.344582

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b873164220d3"
down_revision: str | None = "9b6b53ec45b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "indexing_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_indexing_jobs_repository_id"), "indexing_jobs", ["repository_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_indexing_jobs_repository_id"), table_name="indexing_jobs")
    op.drop_table("indexing_jobs")
