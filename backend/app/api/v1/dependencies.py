"""Shared FastAPI dependencies for API v1: the current-user guard and the
(currently unregistered) OAuth provider extension point.
"""

import uuid

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ForbiddenError, InvalidTokenError, UnauthorizedError
from app.core.request_context import set_user_id
from app.core.security import decode_access_token
from app.database.session import get_db_session
from app.integrations.interfaces import IOAuthProvider
from app.models.user import User

# tokenUrl only affects what Swagger UI's "Authorize" dialog points at; the
# actual login endpoint accepts a JSON body, not the OAuth2 form fields this
# implies (see ADR 0005) - Swagger's own "Try it out" on /auth/login still
# works for getting a token to paste in manually.
_settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{_settings.api_v1_prefix.lstrip('/')}/auth/login",
    auto_error=False,
)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Raises `InvalidTokenError` (never plain `UnauthorizedError`) for
    every failure here — the bearer token/session itself is what's wrong
    in every branch below, which is exactly the "log the user out" signal
    the frontend's global 401 handler keys off of (see InvalidTokenError's
    docstring)."""
    if token is None:
        raise InvalidTokenError("Not authenticated.")

    try:
        payload = decode_access_token(token)
    except UnauthorizedError as exc:
        # decode_access_token raises the generic UnauthorizedError (it's
        # shared with other callers, e.g. the GitHub OAuth state check) —
        # re-raised here as InvalidTokenError since in *this* caller, an
        # expired/malformed JWT unambiguously means the session is dead.
        raise InvalidTokenError(str(exc)) from exc
    subject = payload.get("sub")
    if subject is None:
        raise InvalidTokenError("Invalid authentication token.")
    if payload.get("purpose") is not None:
        # A token minted with a `purpose` (e.g. the GitHub OAuth `state`
        # value — see github_service.get_connect_authorization_url) is
        # scoped to that one flow only. Without this check, a leaked/logged
        # `state` value would work as a fully general bearer token for the
        # rest of the API for its whole (albeit short) lifetime.
        raise InvalidTokenError("This token cannot be used for API authentication.")

    try:
        user_id = uuid.UUID(subject)
    except ValueError as exc:
        raise InvalidTokenError("Invalid authentication token.") from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise InvalidTokenError("User not found or inactive.")

    set_user_id(str(user.id))
    return user


def get_oauth_provider() -> IOAuthProvider | None:
    """Return the registered GitHub OAuth adapter, or None if not configured.

    No concrete `IOAuthProvider` implementation exists yet. When one is
    added under `app.integrations`, construct and return it here — every
    caller of this dependency (see `api/v1/routers/oauth.py`) already
    handles the `None` case by treating GitHub login as not configured.
    """
    return None


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Guard that requires the authenticated user to have the 'admin' role.

    Use as a dependency on routes that should only be accessible to
    administrators (AI Workspace, Tool Registry, Security, Advanced).
    """
    if getattr(current_user, "role", "user") != "admin":
        raise ForbiddenError("Administrator access required.")
    return current_user
