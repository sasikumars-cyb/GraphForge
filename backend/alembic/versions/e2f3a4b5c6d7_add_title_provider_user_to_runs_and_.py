"""add title/provider/user_id to agent_runs, original_prompt to workflows

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-24 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- agent_runs: title (nullable — only standalone runs get one),
    # provider, user_id ---
    op.add_column("agent_runs", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column("agent_runs", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_agent_runs_user_id",
        "agent_runs",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- workflows: original_prompt. Existing rows: title *was* the raw
    # prompt before AI title generation existed, so backfill from it
    # rather than leaving history with an empty prompt, then require it
    # going forward (every create_workflow() call always has one). ---
    op.add_column("workflows", sa.Column("original_prompt", sa.Text(), nullable=True))
    op.execute("UPDATE workflows SET original_prompt = title WHERE original_prompt IS NULL")
    op.alter_column("workflows", "original_prompt", nullable=False)


def downgrade() -> None:
    op.drop_column("workflows", "original_prompt")
    op.drop_constraint("fk_agent_runs_user_id", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "user_id")
    op.drop_column("agent_runs", "provider")
    op.drop_column("agent_runs", "title")
