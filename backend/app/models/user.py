"""The `users` table.

`hashed_password` is nullable and `auth_provider` exists specifically to
accommodate a future GitHub-OAuth-only account (no local password at all) —
see `app.integrations.interfaces.IOAuthProvider` and ADR 0005 for the plan.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Nullable: a user created via a future OAuth provider has no local
    # password at all, not an empty one.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # "local" today; a future GitHub adapter would set this to "github" for
    # accounts created via OAuth. Not an enum yet — one value in production
    # so far, and a DB-level enum migration is cheap to do later if needed.
    auth_provider: Mapped[str] = mapped_column(String(50), nullable=False, default="local")

    # Role-based access control. "user" is the default; "admin" grants access
    # to AI Workspace, Tool Registry, Security, and Advanced settings.
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
