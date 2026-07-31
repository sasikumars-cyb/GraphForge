"""merge oauth app credentials heads with llm invocation and preflight warning heads

Two migrations were independently branched off d8e9f0a1b2c3 (add
human_override to agent_steps): 87a0171b59a6 (GitHub PAT/local repos, via
the TestRail/test-case-uploads/Google Drive/OAuth app credentials chain
this session added) and 44c79114ee64 (LLM invocation persistence, via the
preflight warnings chain). Neither touches a table the other does, so this
merge point makes no schema changes of its own — it only unifies the two
heads so `alembic upgrade head` has a single target again.

Revision ID: e536c0f5976b
Revises: 4d24cbe6bf92, c1a2b3d4e5f6
"""

revision = "e536c0f5976b"
down_revision = ("4d24cbe6bf92", "c1a2b3d4e5f6")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
