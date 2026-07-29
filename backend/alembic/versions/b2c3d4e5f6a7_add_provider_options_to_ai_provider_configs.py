"""add provider_options to ai_provider_configs

Adds a structured `provider_options` JSONB column so provider-specific
settings (Bedrock's region, a future Azure OpenAI deployment/api_version,
a custom-endpoint model family, custom headers, ...) have an explicit home
instead of overloading a generic field with per-provider semantics — e.g.
Bedrock's region previously had nowhere to live but the `base_url` column,
which has no actual URL for that provider (see
app.ai.config.resolver._resolve_provider_options).

Purely additive: nullable, defaults to `{}` at the DB level so every
existing row backfills cleanly with no data migration needed.
`_resolve_provider_options` still falls back to reading a legacy
Bedrock region out of `base_url` when `provider_options` is empty for that
row, so an installation that configured Bedrock before this migration
keeps working unchanged until it's next saved through the UI.

Revision ID: a1b2c3d4e5f6
Revises: d4e5f6a7b8c9
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_configs",
        sa.Column(
            "provider_options",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_provider_configs", "provider_options")
