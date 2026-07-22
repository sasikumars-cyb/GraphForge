"""GitHub OAuth login — NOT implemented yet.

These routes exist so the extension point is real and discoverable (in
Swagger, and at these exact URLs) rather than a 404 that looks like a
missing feature. Both return 501 until a concrete `IOAuthProvider` is
registered by `api.v1.dependencies.get_oauth_provider`.
"""

from fastapi import APIRouter, Depends

from app.api.v1.dependencies import get_oauth_provider
from app.core.exceptions import NotImplementedYetError
from app.integrations.interfaces import IOAuthProvider

router = APIRouter(prefix="/auth/github", tags=["auth"])

_NOT_CONFIGURED_MESSAGE = (
    "GitHub OAuth is not configured yet. Use POST /auth/register and /auth/login for now."
)


@router.get("/login", summary="Start GitHub OAuth login (not implemented yet)")
async def github_login(provider: IOAuthProvider | None = Depends(get_oauth_provider)) -> None:
    if provider is None:
        raise NotImplementedYetError(_NOT_CONFIGURED_MESSAGE)
    # Once a provider is registered, this becomes:
    #   return RedirectResponse(provider.get_authorization_url(state=...))


@router.get("/callback", summary="GitHub OAuth callback (not implemented yet)")
async def github_callback(
    code: str | None = None,
    state: str | None = None,
    provider: IOAuthProvider | None = Depends(get_oauth_provider),
) -> None:
    if provider is None:
        raise NotImplementedYetError(_NOT_CONFIGURED_MESSAGE)
    # Once a provider is registered, this becomes: exchange `code` for a
    # token via provider.exchange_code_for_token, fetch the profile via
    # provider.fetch_user_profile, find-or-create a User with
    # auth_provider="github", and issue a JWT the same way auth.login does.
