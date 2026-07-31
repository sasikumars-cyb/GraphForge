"""The `github_connections` table — one row per user who has connected a
GitHub account for repo access (not login; see ADR 0006).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class GitHubConnection(Base):
    __tablename__ = "github_connections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # One connection per user - reconnecting replaces it rather than adding
    # a second row.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    github_user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    github_username: Mapped[str] = mapped_column(String(255), nullable=False)

    # Encrypted with app.core.crypto (Fernet) - never stored or logged
    # in plaintext. See ADR 0006 for the tradeoff.
    encrypted_access_token: Mapped[str] = mapped_column(String(1024), nullable=False)

    # "oauth" (the original, still-default flow) or "pat" (a pasted personal
    # access token - see github_service.connect_with_pat). Purely
    # informational: every downstream consumer of this row (indexer, impact
    # analysis, agents, git-ops writes) reads the same encrypted_access_token
    # regardless of how it was obtained - this column only drives what the
    # Settings UI displays ("Connected via OAuth" vs "... via personal
    # access token").
    auth_method: Mapped[str] = mapped_column(String(16), nullable=False, server_default="oauth")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
