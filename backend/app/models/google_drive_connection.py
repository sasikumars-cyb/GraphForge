"""The `google_drive_connections` table — one row per user who has
connected a Google account for Drive read access (not login).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class GoogleDriveConnection(Base):
    __tablename__ = "google_drive_connections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # One connection per user - reconnecting replaces it rather than adding
    # a second row (same convention as GitHubConnection).
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    google_email: Mapped[str] = mapped_column(String(255), nullable=False)

    # Encrypted with app.core.crypto (Fernet) - never stored or logged in
    # plaintext, same as GitHubConnection.encrypted_access_token.
    encrypted_access_token: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Unlike GitHub, Google access tokens expire (~1hr) - the refresh
    # token is what get_decrypted_access_token uses to silently renew one
    # before handing it to a caller. Nullable: Google only issues a
    # refresh token on a consent-granting exchange (access_type=offline&
    # prompt=consent — see app.integrations.google_drive); a token grant
    # that somehow arrives without one still gets stored, just can't be
    # auto-renewed once it expires.
    encrypted_refresh_token: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    access_token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
