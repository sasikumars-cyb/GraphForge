"""merge ai_profiles and role/knowledge_connections heads

Two migrations were independently branched off f7a1c93b2e40 (add AI
provider configuration tables): b8e2d40a91c7 (AI profiles/usage) and, via
a3b4c5d6e7f8 (knowledge_connections), b5c6d7e8f9a0 (role + seeded admin).
Neither touches a table the other does, so this merge point makes no
schema changes of its own — it only unifies the two heads so `alembic
upgrade head` has a single target again.

Revision ID: f1e2a3b4c5d6
Revises: b8e2d40a91c7, b5c6d7e8f9a0
"""

revision = "f1e2a3b4c5d6"
down_revision = ("b8e2d40a91c7", "b5c6d7e8f9a0")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
