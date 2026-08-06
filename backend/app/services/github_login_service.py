"""KAN-34 — "Sign in with GitHub": a distinct use case from
`github_service.py`'s "Connect GitHub" (repo access). This creates or
looks up a *local application account* identified by the GitHub profile's
verified email and issues this app's own JWT, the same way
`auth_service.authenticate_user` does for a local (email/password)
account. See `app.integrations.interfaces.IOAuthProvider`'s docstring and
ADR 0006.

Deliberately does NOT auto-link a GitHub sign-in to an existing
`auth_provider="local"` account sharing the same email: this app has no
"connect your accounts" UI, and silently granting GitHub-only access to
what was set up as a password-protected account would be a surprising
trust boundary to cross without the user explicitly asking for it. A user
in that situation is told to log in with their password instead (see
`GitHubAccountIsLocalError`).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError, UnauthorizedError
from app.core.security import create_access_token, decode_access_token
from app.integrations.github import GitHubOAuthProvider
from app.models.user import User
from app.services.oauth_app_config_service import get_credential as get_oauth_app_credential

_STATE_EXPIRY = timedelta(minutes=10)
_LOGIN_STATE_PURPOSE = "github_login_state"


class GitHubLoginNotConfiguredError(AppError):
    """Raised when no GitHub OAuth App credential is configured — mirrors
    `github_service.GitHubNotConfiguredError` for the login use case."""

    status_code = 503
    error_code = "github_login_not_configured"


class GitHubAccountIsLocalError(AppError):
    """Raised when the GitHub profile's email already belongs to a
    `auth_provider="local"` account - see this module's docstring for why
    this isn't auto-linked."""

    status_code = 409
    error_code = "github_account_is_local"


class GitHubEmailUnavailableError(AppError):
    """Raised when GitHub's profile response has no verified email to
    identify an account by, even after the `/user/emails` fallback (see
    `app.integrations.github.fetch_user_profile`) - most commonly a scope
    the user declined to grant."""

    status_code = 400
    error_code = "github_email_unavailable"


async def _build_login_provider(db: AsyncSession) -> GitHubOAuthProvider:
    """Same OAuth App credential resolution as `github_service._build_provider`
    (stored admin credential takes precedence over env vars) - only the
    redirect URI differs, since this lands on `/auth/github/callback`
    rather than `/github/callback`."""
    client_id, client_secret = await get_oauth_app_credential(db, "github")
    settings = get_settings()
    client_id = client_id or settings.github_client_id
    client_secret = client_secret or settings.github_client_secret
    if not client_id or not client_secret:
        raise GitHubLoginNotConfiguredError(
            "GitHub sign-in is not configured. Set GITHUB_CLIENT_ID and "
            "GITHUB_CLIENT_SECRET (see docs/setup.md), or configure it from "
            "Settings -> Integrations as an admin."
        )
    return GitHubOAuthProvider(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=settings.github_login_redirect_uri,
    )


async def get_login_authorization_url(db: AsyncSession) -> str:
    """Builds the GitHub authorize URL for the login flow. `state` here is
    a pure CSRF nonce, not an identity carrier (unlike
    `github_service.get_connect_authorization_url`'s, which encodes the
    already-authenticated user) - there is no user yet at login time."""
    provider = await _build_login_provider(db)
    state = create_access_token(
        subject=str(uuid.uuid4()), expires_delta=_STATE_EXPIRY, purpose=_LOGIN_STATE_PURPOSE
    )
    return provider.get_authorization_url(state)


async def handle_login_callback(db: AsyncSession, code: str, state: str) -> str:
    """Verifies `state`, exchanges `code`, fetches the GitHub profile, and
    finds-or-creates a local `User` for it. Returns a signed access token
    for that user - a real login/signup, not just a connection record.
    """
    payload = decode_access_token(state)
    if payload.get("purpose") != _LOGIN_STATE_PURPOSE:
        raise UnauthorizedError("Invalid OAuth state.")

    provider = await _build_login_provider(db)
    access_token = await provider.exchange_code_for_token(code)
    profile = await provider.fetch_user_profile(access_token)

    if profile.email is None:
        raise GitHubEmailUnavailableError(
            "GitHub did not provide a verified email address. Grant email "
            "access when authorizing, or use email/password login instead."
        )

    result = await db.execute(select(User).where(User.email == profile.email))
    user = result.scalar_one_or_none()

    if user is not None and user.auth_provider != "github":
        raise GitHubAccountIsLocalError(
            "An account with this email already exists. Log in with your password instead."
        )

    if user is None:
        user = User(
            email=profile.email,
            full_name=profile.name or profile.email,
            hashed_password=None,
            auth_provider="github",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    elif not user.is_active:
        raise UnauthorizedError("This account has been deactivated.")

    return create_access_token(subject=str(user.id))
