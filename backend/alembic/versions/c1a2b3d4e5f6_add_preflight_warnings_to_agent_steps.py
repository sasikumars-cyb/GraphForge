"""add preflight_warnings to agent_steps

ADR 0011, OD-1 (see docs/adr/0011-preflight-validation.md — Warning
persistence, decided 2026-07-31): orchestrator-produced, pre-execution
WARNING-severity pre-flight results need a user-visible home distinct from
`evidence` (the agent's own audit trail). Each entry is shaped
`{code, dependency, message, checked_at}`.

Purely additive: NOT NULL with a `'[]'` server default, so every existing
row backfills to an empty list with no data migration — matching the
`provider_options` precedent (alembic/versions/
b2c3d4e5f6a7_add_provider_options_to_ai_provider_configs.py) for adding a
non-nullable JSON column to an existing table.

Revision ID: c1a2b3d4e5f6
Revises: 44c79114ee64
Create Date: 2026-07-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a2b3d4e5f6"
down_revision: str | None = "44c79114ee64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_steps",
        sa.Column(
            "preflight_warnings",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_steps", "preflight_warnings")
