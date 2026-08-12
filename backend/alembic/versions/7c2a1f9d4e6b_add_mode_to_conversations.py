"""add mode to conversations

Migration Assistant reuses the Home page's conversation tables rather
than a second schema — `mode` ("general" | "migration") is the only new
column, distinguishing which grounding/prompt `ConversationService`
applies to a given conversation's turns (see that module's own
docstring). Every existing row is a general Ask GraphForge conversation,
hence the server-side default rather than a backfill.

Revision ID: 7c2a1f9d4e6b
Revises: 1e4af44998a5
Create Date: 2026-08-12 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c2a1f9d4e6b"
down_revision: str | None = "1e4af44998a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="general"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "mode")
