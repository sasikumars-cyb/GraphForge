"""add live_progress to agent_runs

Nullable, best-effort checkpoint of what a still-running Context Discovery
investigation is doing right now — written out-of-band (its own short-lived
session, independent of the run's main transaction) after each reasoning
cycle so `GET /workflows/{id}` can show live progress instead of a bare
spinner while a stage is `in_progress`. Every existing row starts NULL
(nothing was ever written for a completed run before this existed), and a
run that never opts in (every agent except context_discovery, today) simply
never writes it — reading `NULL` is exactly "no live progress to show",
never an error.

Revision ID: 07283b8542c0
Revises: f3a1c9e0b2d4

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "07283b8542c0"
down_revision: str | None = "f3a1c9e0b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("live_progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "live_progress")
