"""'Connect GitHub' — OAuth flow for repo access, and the live repository
list. Selecting/persisting repositories lives in routers/repositories.py.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.config import get_settings
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.github import (
    AvailableRepository,
    GitHubConnectAuthorizationUrl,
    GitHubConnectionStatus,
    GitHubPATConnectRequest,
)
from app.services.github_service import (
    connect_with_pat,
    disconnect,
    get_connect_authorization_url,
    get_connection,
    handle_oauth_callback,
    list_available_repositories,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])


@router.get("/connection", response_model=GitHubConnectionStatus)
async def connection_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GitHubConnectionStatus:
    connection = await get_connection(db, current_user)
    if connection is None:
        return GitHubConnectionStatus(connected=False)
    return GitHubConnectionStatus(
        connected=True,
        github_username=connection.github_username,
        connected_at=connection.created_at,
        auth_method=connection.auth_method,
    )


@router.get("/connect", response_model=GitHubConnectAuthorizationUrl)
async def connect(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GitHubConnectAuthorizationUrl:
    """Returns the GitHub authorize URL — called via a normal (JWT-bearing)
    fetch; the frontend then does a top-level `window.location` navigation
    to it, since the authorize page can't be reached via XHR."""
    return GitHubConnectAuthorizationUrl(
        authorization_url=await get_connect_authorization_url(db, current_user)
    )


@router.post("/connection/pat", response_model=GitHubConnectionStatus)
async def connect_with_personal_access_token(
    body: GitHubPATConnectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GitHubConnectionStatus:
    """PAT alternative to the OAuth /connect + /callback round trip - same
    resulting GitHubConnection row (see connect_with_pat's docstring), so
    every other GitHub-backed feature (repo listing, indexing, analysis,
    agent read/write) works identically regardless of which endpoint a
    user's connection came from."""
    connection, scope_warning = await connect_with_pat(db, current_user, body.token)
    return GitHubConnectionStatus(
        connected=True,
        github_username=connection.github_username,
        connected_at=connection.created_at,
        auth_method=connection.auth_method,
        scope_warning=scope_warning,
    )


@router.get("/callback", include_in_schema=False)
async def callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """GitHub redirects the browser here directly — no Authorization header,
    hence `state` (see get_connect_authorization_url) carrying the user
    identity instead of the get_current_user dependency.

    Failures redirect back to the frontend with an error flag rather than
    raising: the browser is mid-navigation here, not making an XHR a client
    could show a JSON error for.
    """
    settings = get_settings()
    try:
        await handle_oauth_callback(db, code, state)
    except Exception:
        logger.exception("GitHub OAuth callback failed")
        return RedirectResponse(url=f"{settings.frontend_base_url}/settings?github=error")

    return RedirectResponse(url=f"{settings.frontend_base_url}/settings?github=connected")


@router.delete("/connection", status_code=204)
async def disconnect_github(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    await disconnect(db, current_user)


@router.get("/repositories", response_model=list[AvailableRepository])
async def repositories(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[AvailableRepository]:
    """Live list from GitHub, not from our DB — see repositories.py for the
    persisted/selected set."""
    return await list_available_repositories(db, current_user)
