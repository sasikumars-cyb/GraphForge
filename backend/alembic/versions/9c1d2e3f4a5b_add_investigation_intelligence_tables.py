"""add investigation_intelligence tables (ADR 0021)

Investigation Intelligence Phase 1 — `investigation_provider_events` and
`investigation_outcomes`. Deliberately no foreign key to any Engineering
Memory table (`engineering_evidence_packs`, `knowledge_relationships`,
`user_corrections`) — see ADR 0021 "Why this is not Engineering Memory".

Revision ID: 9c1d2e3f4a5b
Revises: 541c9354725a
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "9c1d2e3f4a5b"
down_revision = "541c9354725a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigation_provider_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "sequence",
            sa.Integer(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("investigation_id", sa.String(length=128), nullable=False),
        sa.Column("cycle_number", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("investigation_type", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("action_key", sa.String(length=255), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("declared_cost", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("yielded_evidence", sa.Boolean(), nullable=False),
        sa.Column("necessity_at_selection", sa.String(length=16), nullable=False),
        sa.Column("base_score_at_selection", sa.Float(), nullable=False),
        sa.Column("priority_boost_applied", sa.Float(), nullable=False),
        sa.Column("priority_boost_source", sa.String(length=16), nullable=False),
        sa.Column("confidence_before", sa.Float(), nullable=False),
        sa.Column("confidence_after", sa.Float(), nullable=False),
        sa.Column(
            "state_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequence", name="uq_investigation_provider_events_sequence"),
    )
    op.create_index(
        "ix_investigation_provider_events_scope_capability_provider",
        "investigation_provider_events",
        ["scope_type", "scope_id", "capability", "provider"],
    )
    op.create_index(
        "ix_investigation_provider_events_investigation_id",
        "investigation_provider_events",
        ["investigation_id"],
    )

    op.create_table(
        "investigation_outcomes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "sequence",
            sa.Integer(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("investigation_id", sa.String(length=128), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("investigation_type", sa.String(length=32), nullable=False),
        sa.Column("cycles_used", sa.Integer(), nullable=False),
        sa.Column("terminal_outcome", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "final_capability_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("contradictions_encountered", sa.Integer(), nullable=False),
        sa.Column("contradictions_resolved", sa.Integer(), nullable=False),
        sa.Column("priority_boost_source_used", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequence", name="uq_investigation_outcomes_sequence"),
    )
    op.create_index(
        "ix_investigation_outcomes_scope",
        "investigation_outcomes",
        ["scope_type", "scope_id"],
    )
    op.create_index(
        "ix_investigation_outcomes_investigation_id",
        "investigation_outcomes",
        ["investigation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_investigation_outcomes_investigation_id", table_name="investigation_outcomes")
    op.drop_index("ix_investigation_outcomes_scope", table_name="investigation_outcomes")
    op.drop_table("investigation_outcomes")

    op.drop_index(
        "ix_investigation_provider_events_investigation_id",
        table_name="investigation_provider_events",
    )
    op.drop_index(
        "ix_investigation_provider_events_scope_capability_provider",
        table_name="investigation_provider_events",
    )
    op.drop_table("investigation_provider_events")
