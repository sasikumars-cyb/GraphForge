"""add background_jobs table

Revision ID: a1b2c3d4e5f6
Revises: b3c4d5e6f7a8
Create Date: 2026-08-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("leased_by", sa.String(length=128), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        op.f("ix_background_jobs_job_type"), "background_jobs", ["job_type"], unique=False
    )
    op.create_index(op.f("ix_background_jobs_status"), "background_jobs", ["status"], unique=False)
    op.create_index(
        op.f("ix_background_jobs_lease_expires_at"),
        "background_jobs",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_background_jobs_correlation_id"),
        "background_jobs",
        ["correlation_id"],
        unique=False,
    )
    # The claim query (JobQueue.claim_next) filters on exactly this pair —
    # status plus the two ordering/eligibility columns it reasons about
    # (lease_expires_at for reclaiming, created_at for FIFO ordering) — a
    # composite index matching that predicate keeps a claim O(log n) instead
    # of a sequential scan as the table grows under sustained load.
    op.create_index(
        "ix_background_jobs_status_created_at",
        "background_jobs",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_background_jobs_status_created_at", table_name="background_jobs")
    op.drop_index(op.f("ix_background_jobs_correlation_id"), table_name="background_jobs")
    op.drop_index(op.f("ix_background_jobs_lease_expires_at"), table_name="background_jobs")
    op.drop_index(op.f("ix_background_jobs_status"), table_name="background_jobs")
    op.drop_index(op.f("ix_background_jobs_job_type"), table_name="background_jobs")
    op.drop_table("background_jobs")
