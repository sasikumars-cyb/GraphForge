"""merge live_progress and view_model heads

Two migrations were independently branched off f3a1c9e0b2d4:
07283b8542c0 (agent_runs.live_progress) and 7074ca43c9c9
(workflow_reports.view_model). Neither touches a table the other does,
so this merge point makes no schema changes of its own — it only unifies
the two heads so `alembic upgrade head` has a single target again.

Revision ID: 84d8c8cc43e9
Revises: 07283b8542c0, 7074ca43c9c9
"""

revision = "84d8c8cc43e9"
down_revision = ("07283b8542c0", "7074ca43c9c9")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
