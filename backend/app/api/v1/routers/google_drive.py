"""'Connect Google Drive' — OAuth flow for read access to a user's Drive.

Unlike GitHub, there's no "pick which repos" step here: once connected, a
Drive file/folder just resolves wherever its link is pasted into a task
description (see app.context_pipeline.providers.GoogleDriveProvider) —
the same way a Jira ticket or GitHub PR reference already does.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.config import get_settings
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.google_drive import (
    GoogleDriveConnectAuthorizationUrl,
    GoogleDriveConnectionStatus,
)
from app.services.google_drive_service import (
    disconnect,
    get_connect_authorization_url,
    get_connection,
    handle_oauth_callback,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/google-drive", tags=["google-drive"])


@router.get("/connection", response_model=GoogleDriveConnectionStatus)
async def connection_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GoogleDriveConnectionStatus:
    connection = await get_connection(db, current_user)
    if connection is None:
        return GoogleDriveConnectionStatus(connected=False)
    return GoogleDriveConnectionStatus(
        connected=True,
        google_email=connection.google_email,
        connected_at=connection.created_at,
    )


@router.get("/connect", response_model=GoogleDriveConnectAuthorizationUrl)
async def connect(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GoogleDriveConnectAuthorizationUrl:
    """Returns the Google authorize URL — called via a normal (JWT-bearing)
    fetch; the frontend then does a top-level `window.location` navigation
    to it, since the authorize page can't be reached via XHR (same
    convention as GET /github/connect)."""
    return GoogleDriveConnectAuthorizationUrl(
        authorization_url=await get_connect_authorization_url(db, current_user)
    )


@router.get("/callback", include_in_schema=False)
async def callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """Google redirects the browser here directly — no Authorization
    header, hence `state` carrying the user identity instead of the
    get_current_user dependency (same shape as GitHub's callback)."""
    settings = get_settings()
    try:
        await handle_oauth_callback(db, code, state)
    except Exception:
        logger.exception("Google Drive OAuth callback failed")
        return RedirectResponse(url=f"{settings.frontend_base_url}/settings?google_drive=error")

    return RedirectResponse(url=f"{settings.frontend_base_url}/settings?google_drive=connected")


@router.delete("/connection", status_code=204)
async def disconnect_google_drive(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    await disconnect(db, current_user)
