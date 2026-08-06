"""KAN-34 — "Sign in with GitHub": a real, working GitHub OAuth login/signup
flow, distinct from `routers/github.py`'s "Connect GitHub" (repo access).
See `app.services.github_login_service`'s module docstring and ADR 0006.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.database.session import get_db_session
from app.services.github_login_service import get_login_authorization_url, handle_login_callback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/github", tags=["auth"])


@router.get("/login", summary="Start GitHub OAuth login")
async def github_login(db: AsyncSession = Depends(get_db_session)) -> RedirectResponse:
    """The frontend does a top-level `window.location` navigation here
    (not a fetch — the GitHub authorize page can't be reached via XHR),
    same as `routers/github.py`'s `/connect` -> `/callback` pair."""
    return RedirectResponse(url=await get_login_authorization_url(db))


@router.get("/callback", include_in_schema=False)
async def github_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """GitHub redirects the browser here directly. Success and failure are
    both a redirect back to the frontend, never a raised error the browser
    can't do anything useful with mid-navigation — the frontend's
    `/oauth/callback` route reads `?token=` (store it, done) or `?error=`
    (an `AppError.error_code`, mapped to a message there) from the query
    string."""
    settings = get_settings()
    try:
        access_token = await handle_login_callback(db, code, state)
    except AppError as exc:
        logger.warning("github_login_failed error_code=%s", exc.error_code)
        return RedirectResponse(
            url=f"{settings.frontend_base_url}/oauth/callback?error={exc.error_code}"
        )
    except Exception:
        logger.exception("github_login_failed_unexpectedly")
        return RedirectResponse(
            url=f"{settings.frontend_base_url}/oauth/callback?error=github_login_failed"
        )

    return RedirectResponse(url=f"{settings.frontend_base_url}/oauth/callback?token={access_token}")
