"""Admin-managed OAuth App credentials (GitHub, Google Drive).

These are app-level OAuth Client ID/Secret pairs registered with each
provider for the whole GraphForge installation - every user's "Connect via
OAuth" click authorizes against the same client, then gets their own
per-user token (see app.services.github_service / google_drive_service).
That makes them an operator concern, not a per-user one.

Resolution precedence, most specific wins:

    stored credential (oauth_app_credentials row)
      -> environment variables (GITHUB_CLIENT_ID / GOOGLE_CLIENT_ID etc.)

The env layer is last, never removed - an installation that has configured
nothing in the UI resolves exactly as it did before this table existed.
Read directly from Postgres per call (unlike the AI provider config's
in-memory snapshot) since these are only read on the low-frequency connect/
callback/token-refresh paths, not from ~20 hot call sites.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import TokenDecryptionError, decrypt_secret, encrypt_secret
from app.models.oauth_app_credential import OAuthAppCredential
from app.schemas.oauth_app_config import OAuthAppCredentialStatus

# Matches the provider keys already used throughout
# app.services.{github,google_drive}_service.
PROVIDER_KEYS = ("github", "google_drive")

_ENV_FIELDS: dict[str, tuple[str, str]] = {
    "github": ("github_client_id", "github_client_secret"),
    "google_drive": ("google_client_id", "google_client_secret"),
}


async def _get_row(db: AsyncSession, provider_key: str) -> OAuthAppCredential | None:
    result = await db.execute(
        select(OAuthAppCredential).where(OAuthAppCredential.provider_key == provider_key)
    )
    return result.scalar_one_or_none()


async def get_credential(db: AsyncSession, provider_key: str) -> tuple[str | None, str | None]:
    """The stored (client_id, client_secret) for `provider_key`, decrypted -
    or (None, None) if nothing is stored, so callers fall back to env vars."""
    row = await _get_row(db, provider_key)
    if row is None:
        return None, None
    try:
        return row.client_id, decrypt_secret(row.encrypted_client_secret)
    except TokenDecryptionError:
        # A rotated TOKEN_ENCRYPTION_KEY must not take OAuth connect down
        # entirely - fall back to env vars the same way a missing row does.
        return None, None


async def upsert_credential(
    db: AsyncSession, provider_key: str, client_id: str, client_secret: str
) -> OAuthAppCredential:
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    row = await _get_row(db, provider_key)
    encrypted = encrypt_secret(client_secret)
    if row is None:
        row = OAuthAppCredential(
            provider_key=provider_key, client_id=client_id, encrypted_client_secret=encrypted
        )
        db.add(row)
    else:
        row.client_id = client_id
        row.encrypted_client_secret = encrypted
    await db.commit()
    await db.refresh(row)
    return row


async def delete_credential(db: AsyncSession, provider_key: str) -> None:
    row = await _get_row(db, provider_key)
    if row is not None:
        await db.delete(row)
        await db.commit()


async def get_status(db: AsyncSession, provider_key: str) -> OAuthAppCredentialStatus:
    settings = get_settings()
    env_client_id_field, env_client_secret_field = _ENV_FIELDS[provider_key]
    env_client_id = getattr(settings, env_client_id_field)
    env_client_secret = getattr(settings, env_client_secret_field)

    row = await _get_row(db, provider_key)
    if row is not None:
        return OAuthAppCredentialStatus(
            provider_key=provider_key, configured=True, source="database", client_id=row.client_id
        )
    if env_client_id and env_client_secret:
        return OAuthAppCredentialStatus(
            provider_key=provider_key,
            configured=True,
            source="environment",
            client_id=env_client_id,
        )
    return OAuthAppCredentialStatus(
        provider_key=provider_key, configured=False, source="unset", client_id=None
    )
