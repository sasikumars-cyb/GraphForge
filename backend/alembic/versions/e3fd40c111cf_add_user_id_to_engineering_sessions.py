"""add user_id to engineering_sessions

Revision ID: e3fd40c111cf
Revises: b3c4d5e6f7a8
Create Date: 2026-08-06 00:00:00.000000

KAN-44: Engineering Sessions had no ownership scoping at all - any
authenticated user could read/mutate any session. Product decision:
private per creator, matching every other user-owned resource in the app
(Repository, Workflow, Run).

`user_id` is backfilled from `created_by_participant_id -> participants
.user_id` where that chain resolves to a human Participant - the same
identity `SessionService.create_session` already records at creation time,
just not directly on the Session row until now. A session whose creator
can't be resolved this way (an agent-only session, or a row predating
Participant's own user_id-backed identity) is left with `user_id IS NULL`
and stays visible to any authenticated user rather than becoming
permanently inaccessible to everyone - the same fallback rule
`agent_runs.py`'s `_run_ownership_clause` already applies to legacy rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3fd40c111cf"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("engineering_sessions", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_engineering_sessions_user_id"),
        "engineering_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_engineering_sessions_user_id_users",
        "engineering_sessions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE engineering_sessions
        SET user_id = participants.user_id
        FROM participants
        WHERE engineering_sessions.created_by_participant_id = participants.id
          AND participants.user_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_engineering_sessions_user_id_users", "engineering_sessions", type_="foreignkey"
    )
    op.drop_index(op.f("ix_engineering_sessions_user_id"), table_name="engineering_sessions")
    op.drop_column("engineering_sessions", "user_id")
