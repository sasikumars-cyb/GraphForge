"""add domain to repositories (ADR 0023)

Nullable repository-grouping field — manual assignment only, no
inference. Every existing row starts ungrouped (domain=NULL); the
Architecture summary endpoint surfaces that as the "Ungrouped" bucket.

Revision ID: f3a1c9e0b2d4
Revises: 9c1d2e3f4a5b

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a1c9e0b2d4"
down_revision: str | None = "9c1d2e3f4a5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("domain", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("repositories", "domain")
