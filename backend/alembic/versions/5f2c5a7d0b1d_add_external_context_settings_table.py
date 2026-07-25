"""add_external_context_settings_table

Revision ID: 5f2c5a7d0b1d
Revises: f7a1c93b2e40
Create Date: 2026-07-25 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "5f2c5a7d0b1d"
down_revision = "f7a1c93b2e40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_context_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False, server_default="workspace"),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("external_context_settings")
