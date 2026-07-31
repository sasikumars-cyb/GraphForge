"""add auth_method to github_connections

Lets a GitHubConnection record whether it came from the OAuth "Connect"
flow or a pasted personal access token (app.services.github_service.
connect_with_pat) - display-only, every downstream reader of
encrypted_access_token is unaffected either way (see the model's own
docstring). Defaulted to 'oauth' so every existing row - all OAuth in
origin - is valid with no backfill.

Revision ID: 87a0171b59a6
Revises: d8e9f0a1b2c3
Create Date: 2026-07-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "87a0171b59a6"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "github_connections",
        sa.Column("auth_method", sa.String(length=16), nullable=False, server_default="oauth"),
    )


def downgrade() -> None:
    op.drop_column("github_connections", "auth_method")
