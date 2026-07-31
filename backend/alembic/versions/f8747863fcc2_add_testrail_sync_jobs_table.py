"""add testrail sync jobs table

Revision ID: f8747863fcc2
Revises: 18e9c24cf3ab
Create Date: 2026-07-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8747863fcc2"
down_revision: str | None = "18e9c24cf3ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "testrail_sync_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("testrail_project_id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_testrail_sync_jobs_testrail_project_id"),
        "testrail_sync_jobs",
        ["testrail_project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_testrail_sync_jobs_testrail_project_id"), table_name="testrail_sync_jobs"
    )
    op.drop_table("testrail_sync_jobs")
