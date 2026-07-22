"""Shared FastAPI dependencies for API v1: the current-user guard and the
(currently unregistered) OAuth provider extension point.
"""

import uuid

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
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
    if token is None:
        raise UnauthorizedError("Not authenticated.")

    payload = decode_access_token(token)
    subject = payload.get("sub")
    if subject is None:
        raise UnauthorizedError("Invalid authentication token.")

    try:
        user_id = uuid.UUID(subject)
    except ValueError as exc:
        raise UnauthorizedError("Invalid authentication token.") from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive.")

    return user


def get_oauth_provider() -> IOAuthProvider | None:
    """Return the registered GitHub OAuth adapter, or None if not configured.

    No concrete `IOAuthProvider` implementation exists yet. When one is
    added under `app.integrations`, construct and return it here — every
    caller of this dependency (see `api/v1/routers/oauth.py`) already
    handles the `None` case by treating GitHub login as not configured.
    """
    return None
