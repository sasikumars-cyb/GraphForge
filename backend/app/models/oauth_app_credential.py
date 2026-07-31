"""Instance-wide OAuth App credentials (GitHub, Google Drive), configurable
from the Settings UI instead of only `backend/.env`.

One row per provider (`provider_key` unique). These are the app-level OAuth
Client ID/Secret registered with the provider for the whole GraphForge
installation — every user's "Connect via OAuth" click uses the same client,
then gets their own per-user token stored in `GitHubConnection` /
`GoogleDriveConnection`. That makes this an operator concern, not a per-user
one, hence admin-gated (see app.api.v1.routers.oauth_apps).

Secrets follow the same pattern as `AIProviderConfig.encrypted_api_key`:
encrypted at rest with app.core.crypto (Fernet), never returned to the
frontend. This table is optional — when a provider has no row, resolution
falls back to `GITHUB_CLIENT_ID`/`GOOGLE_CLIENT_ID` etc. in environment
settings, so existing installations behave identically until someone
configures a provider in the UI.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class OAuthAppCredential(Base):
    """One provider's app-level OAuth Client ID/Secret."""

    __tablename__ = "oauth_app_credentials"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # "github" | "google_drive" — matches the provider keys already used
    # throughout app.services.{github,google_drive}_service.
    provider_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    # Not secret (it's sent to the browser during the OAuth redirect anyway)
    # so stored in plaintext, same as GitHub/Google client IDs always are.
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Encrypted with app.core.crypto (Fernet) - never logged, never
    # serialized to an API response.
    encrypted_client_secret: Mapped[str] = mapped_column(String(1024), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
