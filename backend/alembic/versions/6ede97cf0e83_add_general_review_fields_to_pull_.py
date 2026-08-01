"""add general review fields to pull_request_ai_analyses

Revision ID: 6ede97cf0e83
Revises: ba8b3fb42d8a
Create Date: 2026-08-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6ede97cf0e83"
down_revision: str | None = "ba8b3fb42d8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pull_request_ai_analyses", sa.Column("quality_score", sa.Float(), nullable=True)
    )
    op.add_column("pull_request_ai_analyses", sa.Column("risk_score", sa.Float(), nullable=True))
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("merge_recommendation", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("findings", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("architecture_observations", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("maintainability_observations", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("reliability_observations", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("testing_review", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("documentation_review", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("positive_findings", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("suggested_improvements", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "pull_request_ai_analyses", sa.Column("security_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "pull_request_ai_analyses", sa.Column("testing_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "pull_request_ai_analyses", sa.Column("documentation_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "pull_request_ai_analyses", sa.Column("architecture_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "pull_request_ai_analyses", sa.Column("performance_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "pull_request_ai_analyses", sa.Column("maintainability_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "pull_request_ai_analyses",
        sa.Column("file_reviews", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("pull_request_ai_analyses", "file_reviews")
    op.drop_column("pull_request_ai_analyses", "maintainability_score")
    op.drop_column("pull_request_ai_analyses", "performance_score")
    op.drop_column("pull_request_ai_analyses", "architecture_score")
    op.drop_column("pull_request_ai_analyses", "documentation_score")
    op.drop_column("pull_request_ai_analyses", "testing_score")
    op.drop_column("pull_request_ai_analyses", "security_score")
    op.drop_column("pull_request_ai_analyses", "suggested_improvements")
    op.drop_column("pull_request_ai_analyses", "positive_findings")
    op.drop_column("pull_request_ai_analyses", "documentation_review")
    op.drop_column("pull_request_ai_analyses", "testing_review")
    op.drop_column("pull_request_ai_analyses", "reliability_observations")
    op.drop_column("pull_request_ai_analyses", "maintainability_observations")
    op.drop_column("pull_request_ai_analyses", "architecture_observations")
    op.drop_column("pull_request_ai_analyses", "findings")
    op.drop_column("pull_request_ai_analyses", "merge_recommendation")
    op.drop_column("pull_request_ai_analyses", "risk_score")
    op.drop_column("pull_request_ai_analyses", "quality_score")
