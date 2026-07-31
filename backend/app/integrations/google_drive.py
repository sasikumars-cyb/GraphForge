"""A real, working Google Drive OAuth adapter.

Used for "Connect Google Drive" (read-only file access via a pasted Drive
link, resolved the same way a Jira ticket or GitHub PR reference is — see
app.context_pipeline.providers.GoogleDriveProvider). Talks to real Google
endpoints (accounts.google.com, oauth2.googleapis.com,
www.googleapis.com). This module makes no assumption about *whose* OAuth
Client is configured — that's GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET in
core.config, a Google Cloud Console OAuth Client the operator provisions,
same as GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET.

Unlike GitHub's OAuth tokens (which don't expire), Google access tokens
expire in ~1 hour — `exchange_code_for_token` returns a refresh token
alongside the access token, and `refresh_access_token` exchanges it for a
fresh one. `access_type=offline&prompt=consent` on the authorize URL is
what makes Google actually issue a refresh token at all; omitting either
is a common mistake that silently produces an access-only grant.
"""

from dataclasses import dataclass

import httpx

from app.core.exceptions import AppError

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# drive.readonly: read access to the user's Drive (paste-a-URL access
# model — see app.knowledge.registry's google_drive TransportSpec
# docstring for why this isn't the narrower drive.file scope). userinfo.
# email: for "Connected as x@gmail.com" display, same purpose read:user
# serves for GitHub.
_SCOPE = (
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/userinfo.email"
)


class GoogleDriveApiError(AppError):
    """Raised when Google's OAuth or Drive API returns an error response."""

    status_code = 502
    error_code = "google_drive_api_error"


@dataclass(frozen=True)
class GoogleTokenGrant:
    access_token: str
    refresh_token: str | None
    expires_in: int


@dataclass(frozen=True)
class GoogleUserProfile:
    email: str


class GoogleDriveOAuthProvider:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def get_authorization_url(self, state: str) -> str:
        params = httpx.QueryParams(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": _SCOPE,
                "state": state,
                # Both required to get a refresh_token back — Google only
                # issues one on a consent-granting exchange, not on every
                # exchange (see module docstring).
                "access_type": "offline",
                "prompt": "consent",
            }
        )
        return f"{_AUTHORIZE_URL}?{params}"

    async def exchange_code_for_token(self, code: str) -> GoogleTokenGrant:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        body = response.json()
        if response.is_error or "error" in body:
            reason = body.get("error_description", body.get("error", response.text))
            raise GoogleDriveApiError(f"Google token exchange failed: {reason}")

        access_token = body.get("access_token")
        if not access_token:
            raise GoogleDriveApiError("Google token exchange response had no access_token.")
        return GoogleTokenGrant(
            access_token=str(access_token),
            refresh_token=body.get("refresh_token"),
            expires_in=int(body.get("expires_in", 3600)),
        )

    async def refresh_access_token(self, refresh_token: str) -> GoogleTokenGrant:
        """Exchange a stored refresh token for a fresh access token. Google
        does not return a new refresh token on this call — the original
        one keeps working until the user revokes access."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        body = response.json()
        if response.is_error or "error" in body:
            reason = body.get("error_description", body.get("error", response.text))
            raise GoogleDriveApiError(f"Google token refresh failed: {reason}")

        access_token = body.get("access_token")
        if not access_token:
            raise GoogleDriveApiError("Google token refresh response had no access_token.")
        return GoogleTokenGrant(
            access_token=str(access_token),
            refresh_token=refresh_token,
            expires_in=int(body.get("expires_in", 3600)),
        )

    async def fetch_user_profile(self, access_token: str) -> GoogleUserProfile:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                _USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
        if response.is_error:
            raise GoogleDriveApiError(
                f"Google userinfo request failed with status {response.status_code}: "
                f"{response.text}"
            )
        data = response.json()
        return GoogleUserProfile(email=str(data.get("email", "")))
