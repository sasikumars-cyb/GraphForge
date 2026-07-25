"""add ai profiles, provider usage, and default profile

Adds the AI Profile abstraction (`ai_profiles`), per-provider usage counters
(`ai_provider_usage`), and a `default_profile_slug` column on `ai_settings`.

Workflow-stage mapping deliberately reuses the existing
`ai_settings.stage_overrides` JSONB rather than adding a second mapping
table — a stage entry may now carry `{"profile": "<slug>"}` alongside the
existing `{"provider": ..., "model": ...}` form.

Revision ID: b8e2d40a91c7
Revises: f7a1c93b2e40
"""

import sqlalchemy as sa

from alembic import op

revision = "b8e2d40a91c7"
down_revision = "f7a1c93b2e40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_level", sa.String(length=32), nullable=True),
        sa.Column("structured_output", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("streaming", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fallback_profile_slug", sa.String(length=64), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "ai_provider_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_limit_events", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auth_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rate_limit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key"),
    )

    op.add_column(
        "ai_settings",
        sa.Column("default_profile_slug", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_settings", "default_profile_slug")
    op.drop_table("ai_provider_usage")
    op.drop_table("ai_profiles")
