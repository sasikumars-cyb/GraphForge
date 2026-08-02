"""add engineering memory tables (RFC-04)

Revision ID: 09a3fe03cce9
Revises: 6ede97cf0e83
Create Date: 2026-08-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "09a3fe03cce9"
down_revision: str | None = "6ede97cf0e83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engineering_evidence_packs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pack_id", sa.String(length=512), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("commit_sha", sa.String(length=255), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("is_delta", sa.Boolean(), nullable=False),
        sa.Column("base_pack_id", sa.String(length=512), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("compressed_blob", sa.LargeBinary(), nullable=False),
        sa.Column("compression", sa.String(length=16), nullable=False),
        sa.Column("uncompressed_size_bytes", sa.Integer(), nullable=False),
        sa.Column("produced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_engineering_evidence_packs_repo_commit_schema",
        "engineering_evidence_packs",
        ["repository_id", "commit_sha", "schema_version"],
    )
    op.create_index(
        op.f("ix_engineering_evidence_packs_pack_id"),
        "engineering_evidence_packs",
        ["pack_id"],
    )
    op.create_index(
        op.f("ix_engineering_evidence_packs_repository_id"),
        "engineering_evidence_packs",
        ["repository_id"],
    )

    op.create_table(
        "knowledge_relationships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "sequence",
            sa.Integer(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("relationship_key", sa.String(length=1024), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_type", sa.String(length=128), nullable=False),
        sa.Column("source_entity", sa.String(length=1024), nullable=False),
        sa.Column("target_entity", sa.String(length=1024), nullable=False),
        sa.Column("hypothesis_ids", sa.JSON(), nullable=False),
        sa.Column("confidence_state", sa.String(length=32), nullable=False),
        sa.Column("distinct_confirming_source_types", sa.Integer(), nullable=False),
        sa.Column("confirming_source_types", sa.JSON(), nullable=False),
        sa.Column("max_confirming_reliability_tier", sa.Integer(), nullable=False),
        sa.Column("contradiction_count", sa.Integer(), nullable=False),
        sa.Column("confidence_formula_version", sa.String(length=32), nullable=False),
        sa.Column("confidence_computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_knowledge_relationships_key_sequence",
        "knowledge_relationships",
        ["relationship_key", "sequence"],
    )
    op.create_index(
        op.f("ix_knowledge_relationships_relationship_key"),
        "knowledge_relationships",
        ["relationship_key"],
    )
    op.create_index(
        op.f("ix_knowledge_relationships_repository_id"),
        "knowledge_relationships",
        ["repository_id"],
    )

    op.create_table(
        "user_corrections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("relationship_key", sa.String(length=1024), nullable=False),
        sa.Column("correction_source_kind", sa.String(length=16), nullable=False),
        sa.Column("correction_source_identity", sa.String(length=255), nullable=False),
        sa.Column("correction_source_trust_level", sa.Float(), nullable=False),
        sa.Column("corrected_state", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("correction_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "persisted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_corrections_relationship_key"),
        "user_corrections",
        ["relationship_key"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_corrections_relationship_key"), table_name="user_corrections")
    op.drop_table("user_corrections")

    op.drop_index(
        op.f("ix_knowledge_relationships_repository_id"), table_name="knowledge_relationships"
    )
    op.drop_index(
        op.f("ix_knowledge_relationships_relationship_key"), table_name="knowledge_relationships"
    )
    op.drop_index("ix_knowledge_relationships_key_sequence", table_name="knowledge_relationships")
    op.drop_table("knowledge_relationships")

    op.drop_index(
        op.f("ix_engineering_evidence_packs_repository_id"), table_name="engineering_evidence_packs"
    )
    op.drop_index(
        op.f("ix_engineering_evidence_packs_pack_id"), table_name="engineering_evidence_packs"
    )
    op.drop_index(
        "ix_engineering_evidence_packs_repo_commit_schema", table_name="engineering_evidence_packs"
    )
    op.drop_table("engineering_evidence_packs")
