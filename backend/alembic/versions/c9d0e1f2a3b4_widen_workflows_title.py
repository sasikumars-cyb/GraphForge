"""widen workflows.title from varchar(512) to text

Revision ID: c9d0e1f2a3b4
Revises: b4c5d6e7f8a9
Create Date: 2026-07-24 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NewWorkflowPage's "What's the engineering objective?" textarea invites
    # a full multi-paragraph brief, not a short title — VARCHAR(512) was too
    # small for how this field is actually used. TEXT is unbounded at the DB
    # layer; the real ceiling is the API's own max_length (see
    # CreateWorkflowRequest.title), so this migration never needs a repeat.
    op.alter_column(
        "workflows",
        "title",
        existing_type=sa.String(length=512),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "workflows",
        "title",
        existing_type=sa.Text(),
        type_=sa.String(length=512),
        existing_nullable=False,
    )
