"""add learning_events table (RFC-07)

Revision ID: b3c4d5e6f7a8
Revises: c7d8e9f0a1b2
Create Date: 2026-08-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "sequence",
            sa.Integer(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("relationship_key", sa.String(length=1024), nullable=True),
        sa.Column("relationship_type", sa.String(length=128), nullable=True),
        sa.Column("generator_names", sa.JSON(), nullable=False),
        sa.Column("confidence_state_at_event", sa.String(length=32), nullable=True),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("source_identity", sa.String(length=255), nullable=False),
        sa.Column("source_trust_level", sa.Float(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("event_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "persisted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(op.f("ix_learning_events_repository_id"), "learning_events", ["repository_id"])
    op.create_index(op.f("ix_learning_events_event_type"), "learning_events", ["event_type"])
    op.create_index(
        op.f("ix_learning_events_relationship_key"), "learning_events", ["relationship_key"]
    )
    op.create_index(
        op.f("ix_learning_events_relationship_type"), "learning_events", ["relationship_type"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_learning_events_relationship_type"), table_name="learning_events")
    op.drop_index(op.f("ix_learning_events_relationship_key"), table_name="learning_events")
    op.drop_index(op.f("ix_learning_events_event_type"), table_name="learning_events")
    op.drop_index(op.f("ix_learning_events_repository_id"), table_name="learning_events")
    op.drop_table("learning_events")
