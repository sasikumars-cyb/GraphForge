"""Admin-managed OAuth App credentials (GitHub, Google Drive) - lets an
operator set the app-level Client ID/Secret from Settings instead of only
`backend/.env` + a container restart. Admin-gated: unlike a user's own
"Connect via OAuth" (per-user, app.api.v1.routers.{github,google_drive}),
these credentials are shared by every user's OAuth flow on this
installation - see app.models.oauth_app_credential.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.oauth_app_config import OAuthAppCredentialStatus, OAuthAppCredentialUpdate
from app.services.oauth_app_config_service import (
    PROVIDER_KEYS,
    delete_credential,
    get_status,
    upsert_credential,
)

router = APIRouter(prefix="/oauth-apps", tags=["oauth-apps"])


@router.get("", response_model=list[OAuthAppCredentialStatus])
async def list_oauth_app_credentials(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> list[OAuthAppCredentialStatus]:
    return [await get_status(db, key) for key in PROVIDER_KEYS]


@router.put("/{provider_key}", response_model=OAuthAppCredentialStatus)
async def update_oauth_app_credential(
    provider_key: str,
    body: OAuthAppCredentialUpdate,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> OAuthAppCredentialStatus:
    if provider_key not in PROVIDER_KEYS:
        raise NotFoundError(f"Unknown OAuth app provider '{provider_key}'.")
    await upsert_credential(db, provider_key, body.client_id, body.client_secret)
    return await get_status(db, provider_key)


@router.delete("/{provider_key}", response_model=OAuthAppCredentialStatus)
async def clear_oauth_app_credential(
    provider_key: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> OAuthAppCredentialStatus:
    """Clears the stored override, falling back to environment variables
    (if any) rather than requiring one to be re-entered to undo a mistake."""
    if provider_key not in PROVIDER_KEYS:
        raise NotFoundError(f"Unknown OAuth app provider '{provider_key}'.")
    await delete_credential(db, provider_key)
    return await get_status(db, provider_key)
