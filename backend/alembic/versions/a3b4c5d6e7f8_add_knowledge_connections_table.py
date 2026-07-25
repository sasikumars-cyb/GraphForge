"""add knowledge_connections table

Multi-connection architecture for Knowledge Sources. Each row represents one
configured connection to a source system deployment (GitHub Enterprise,
Open Source GitHub, Engineering Jira, etc.).

Revision ID: a3b4c5d6e7f8
Revises: a2b3c4d5e6f7
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("auth_method", sa.String(length=32), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("encrypted_credentials", sa.String(length=4096), nullable=True),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_connections_source_type",
        "knowledge_connections",
        ["source_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_connections_source_type", table_name="knowledge_connections")
    op.drop_table("knowledge_connections")
