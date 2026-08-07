"""merge last_indexed tracking and background_jobs heads

Two migrations were independently branched off b3c4d5e6f7a8 (add user_id
to engineering_sessions): d600ead2cff3 (background_jobs table), and via
c7d8e9f0a1b2 → ... → 8f2d1b3d9024, 3a7c1e9f4b52 (last_indexed tracking on
repositories, KAN-32). Neither touches a table the other does, so this
merge point makes no schema changes of its own — it only unifies the two
heads so `alembic upgrade head` has a single target again.

Revision ID: 541c9354725a
Revises: 3a7c1e9f4b52, d600ead2cff3
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "541c9354725a"
down_revision: str | tuple[str, ...] | None = ("3a7c1e9f4b52", "d600ead2cff3")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
