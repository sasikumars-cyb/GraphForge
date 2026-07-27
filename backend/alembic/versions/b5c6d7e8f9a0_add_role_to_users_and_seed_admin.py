"""add role to users and seed admin account

Adds a `role` column to the users table (default 'user') and creates a
bootstrap admin account for development.

WARNING: The admin/admin credentials are for LOCAL DEVELOPMENT ONLY.
Any production deployment MUST change or remove this account.

Revision ID: b5c6d7e8f9a0
Revises: a3b4c5d6e7f8
"""

import uuid

import bcrypt
import sqlalchemy as sa

from alembic import op

revision = "b5c6d7e8f9a0"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

# Development bootstrap credentials. NOT for production use.
_ADMIN_EMAIL = "admin@graphforge.local"
_ADMIN_PASSWORD = "admin"
_ADMIN_NAME = "GraphForge Admin"


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def upgrade() -> None:
    # Add role column with a safe default.
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=32), nullable=False, server_default="user"),
    )

    # Seed the development admin account — never in production. A known
    # admin/admin credential is safe only because local dev/demo databases
    # are throwaway; running this same INSERT against a production database
    # would hand out real admin access to anyone who knows this migration
    # exists (it's public, in this repo). `ENVIRONMENT=production` is the
    # same settings field app/core/config.py's insecure-default guard
    # checks — skip the seed there instead of trusting every operator to
    # remember to change/remove it afterward.
    from app.core.config import get_settings

    if get_settings().environment != "production":
        op.execute(
            sa.text(
                """
                INSERT INTO users (id, email, full_name, hashed_password, auth_provider, role, is_active)
                VALUES (:id, :email, :full_name, :hashed_password, 'local', 'admin', true)
                ON CONFLICT (email) DO UPDATE SET role = 'admin'
                """
            ).bindparams(
                sa.bindparam("id", value=uuid.uuid4(), type_=sa.Uuid()),
                email=_ADMIN_EMAIL,
                full_name=_ADMIN_NAME,
                hashed_password=_hash_password(_ADMIN_PASSWORD),
            )
        )


def downgrade() -> None:
    # Remove the seeded admin (best effort).
    op.execute(sa.text("DELETE FROM users WHERE email = :email").bindparams(email=_ADMIN_EMAIL))
    op.drop_column("users", "role")
