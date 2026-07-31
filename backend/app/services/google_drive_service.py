"""Connect Google Drive and resolve a user's access token — the Drive
counterpart to app.services.github_service. No repository-style "pick
what to track" step here: a connected user's Drive links just resolve
wherever they're pasted into a task description (see
app.context_pipeline.providers.GoogleDriveProvider), the same way a Jira
ticket or GitHub PR reference already does.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.exceptions import AppError, NotFoundError, UnauthorizedError
from app.core.security import create_access_token, decode_access_token
from app.integrations.google_drive import GoogleDriveOAuthProvider
from app.models.google_drive_connection import GoogleDriveConnection
from app.models.user import User
from app.services.oauth_app_config_service import get_credential as get_oauth_app_credential

_STATE_EXPIRY = timedelta(minutes=10)
_OAUTH_STATE_PURPOSE = "google_drive_oauth_state"
# Refresh a bit before actual expiry so a request never races a token that
# expires mid-flight (a Drive fetch that starts with 30s left could easily
# still be in progress when the token actually dies).
_REFRESH_SKEW = timedelta(minutes=2)


class GoogleDriveNotConfiguredError(AppError):
    """Raised when GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET aren't set."""

    status_code = 503
    error_code = "google_drive_not_configured"


async def _build_provider(db: AsyncSession) -> GoogleDriveOAuthProvider:
    """A stored OAuth App credential (set via Settings -> Integrations by an
    admin, see app.services.oauth_app_config_service) takes precedence over
    GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET - the env vars remain the fallback
    for installs that configure this way instead."""
    client_id, client_secret = await get_oauth_app_credential(db, "google_drive")
    settings = get_settings()
    client_id = client_id or settings.google_client_id
    client_secret = client_secret or settings.google_client_secret
    if not client_id or not client_secret:
        raise GoogleDriveNotConfiguredError(
            "Google Drive integration is not configured. Set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET, or configure it from Settings -> Integrations as "
            "an admin."
        )
    return GoogleDriveOAuthProvider(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=settings.google_oauth_redirect_uri,
    )


async def get_connection(db: AsyncSession, user: User) -> GoogleDriveConnection | None:
    result = await db.execute(
        select(GoogleDriveConnection).where(GoogleDriveConnection.user_id == user.id)
    )
    return result.scalar_one_or_none()


async def get_connect_authorization_url(db: AsyncSession, user: User) -> str:
    provider = await _build_provider(db)
    state = create_access_token(
        subject=str(user.id), expires_delta=_STATE_EXPIRY, purpose=_OAUTH_STATE_PURPOSE
    )
    return provider.get_authorization_url(state)


async def handle_oauth_callback(db: AsyncSession, code: str, state: str) -> User:
    payload = decode_access_token(state)
    if payload.get("purpose") != _OAUTH_STATE_PURPOSE:
        raise UnauthorizedError("Invalid OAuth state.")
    subject = payload.get("sub")
    if subject is None:
        raise UnauthorizedError("Invalid OAuth state.")

    user = await db.get(User, uuid.UUID(subject))
    if user is None:
        raise NotFoundError("User not found.")

    provider = await _build_provider(db)
    grant = await provider.exchange_code_for_token(code)
    profile = await provider.fetch_user_profile(grant.access_token)
    expires_at = datetime.now(UTC) + timedelta(seconds=grant.expires_in)

    connection = await get_connection(db, user)
    encrypted_access = encrypt_secret(grant.access_token)
    # Google only issues a refresh token on a consent-granting exchange —
    # a reconnect that Google treats as already-consented can omit it.
    # Keep whatever refresh token is already stored rather than clobbering
    # it with None in that case.
    encrypted_refresh = (
        encrypt_secret(grant.refresh_token)
        if grant.refresh_token
        else (connection.encrypted_refresh_token if connection else None)
    )

    if connection is None:
        connection = GoogleDriveConnection(
            user_id=user.id,
            google_email=profile.email,
            encrypted_access_token=encrypted_access,
            encrypted_refresh_token=encrypted_refresh,
            access_token_expires_at=expires_at,
        )
        db.add(connection)
    else:
        connection.google_email = profile.email
        connection.encrypted_access_token = encrypted_access
        connection.encrypted_refresh_token = encrypted_refresh
        connection.access_token_expires_at = expires_at

    await db.commit()
    return user


async def disconnect(db: AsyncSession, user: User) -> None:
    connection = await get_connection(db, user)
    if connection is not None:
        await db.delete(connection)
        await db.commit()


async def get_decrypted_access_token(db: AsyncSession, user_id: uuid.UUID) -> str | None:
    """Resolve a user's Google Drive access token, decrypted and
    guaranteed fresh — or `None` if they haven't connected. Transparently
    refreshes and persists a new access token when the stored one is at
    or past expiry (minus `_REFRESH_SKEW`), so every caller (just
    `GoogleDriveTool` today) gets a token that works without knowing
    refresh is a thing — the same "always valid" guarantee
    `github_service.get_decrypted_access_token` gives callers, which
    GitHub tokens satisfy by never expiring and this satisfies by
    renewing.
    """
    result = await db.execute(
        select(GoogleDriveConnection).where(GoogleDriveConnection.user_id == user_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        return None

    if datetime.now(UTC) < connection.access_token_expires_at - _REFRESH_SKEW:
        return decrypt_secret(connection.encrypted_access_token)

    if not connection.encrypted_refresh_token:
        # Expired with no way to renew - the stored (now-stale) token is
        # still returned; the caller's request will fail with a real 401
        # from Google, which is a clearer signal than raising here for a
        # token that might, in practice, still have a few seconds left.
        return decrypt_secret(connection.encrypted_access_token)

    provider = await _build_provider(db)
    grant = await provider.refresh_access_token(decrypt_secret(connection.encrypted_refresh_token))
    connection.encrypted_access_token = encrypt_secret(grant.access_token)
    connection.access_token_expires_at = datetime.now(UTC) + timedelta(seconds=grant.expires_in)
    await db.commit()
    return grant.access_token
