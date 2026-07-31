"""add source to repositories

Lets a Repository row record whether it came from GitHub or a local
filesystem path (app.services.local_repository_service) - display-only
plus the occasional GitHub-only branch (e.g. PR ingestion); indexing
itself works identically for both sources already (html_url is all the
cloner needs - see indexer.scanner.repository_cloner). Defaulted to
'github' so every existing row - all GitHub in origin - is valid with no
backfill.

Revision ID: 18e9c24cf3ab
Revises: 87a0171b59a6
Create Date: 2026-07-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "18e9c24cf3ab"
down_revision: str | None = "87a0171b59a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="github"),
    )


def downgrade() -> None:
    op.drop_column("repositories", "source")
