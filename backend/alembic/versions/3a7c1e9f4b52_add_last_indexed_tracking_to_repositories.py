"""add last_indexed tracking to repositories (KAN-32)

Revision ID: 3a7c1e9f4b52
Revises: 8f2d1b3d9024
Create Date: 2026-08-07 00:00:00.000000

Adds `last_indexed_commit_sha`/`last_indexed_language`/`last_indexed_at` to
`repositories` — what incremental indexing (KAN-32) diffs the next push
against, and which parser to use without re-cloning just to detect the
language again. Purely additive, all nullable: an existing row reads as
"never indexed under this scheme yet," which is exactly correct (nothing
before this migration recorded a commit sha) and is the same signal
`run_indexing` already treats as "do a full index" for a brand-new
repository — no backfill needed, no existing row's meaning changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3a7c1e9f4b52"
down_revision: str | None = "8f2d1b3d9024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories", sa.Column("last_indexed_commit_sha", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "repositories", sa.Column("last_indexed_language", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "repositories",
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("repositories", "last_indexed_at")
    op.drop_column("repositories", "last_indexed_language")
    op.drop_column("repositories", "last_indexed_commit_sha")
