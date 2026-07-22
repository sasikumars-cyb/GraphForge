"""A real, working GitHub OAuth adapter — implements `IOAuthProvider`.

Used today for "Connect GitHub" (repo access). NOT wired to `/auth/github/login`
(signing in AS a GitHub identity) — that remains a separate, unimplemented use
case; see ADR 0006.

Talks to real GitHub endpoints (`github.com`, `api.github.com`). This module
makes no assumption about *whose* GitHub OAuth App is configured — that's
`GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` in `core.config`, which must be the
user's own personal OAuth App, never a company-owned one.
"""

from typing import Any

import httpx

from app.core.exceptions import AppError, NotImplementedYetError
from app.integrations.interfaces import (
    ChangedFile,
    IOAuthProvider,
    IVersionControlProvider,
    OAuthUserProfile,
    RepositoryInfo,
)

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_API_BASE = "https://api.github.com"

# `repo` covers listing + reading both public and private repos the user
# grants access to; `read:user` gets us their login/profile for
# GitHubConnection.github_username.
_SCOPE = "repo read:user"

_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class GitHubApiError(AppError):
    """Raised when GitHub's OAuth or REST API returns an error response."""

    status_code = 502
    error_code = "github_api_error"


async def _github_get(
    path: str,
    access_token: str | None,
    params: dict[str, str | int] | None = None,
) -> Any:
    """Shared authenticated-GET helper for every GitHub REST call in this
    module. `access_token` is optional — public repos/resources are
    readable unauthenticated too, just subject to GitHub's lower
    unauthenticated rate limit."""
    headers = dict(_API_HEADERS)
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{_API_BASE}{path}", headers=headers, params=params)

    if response.is_error:
        raise GitHubApiError(
            f"GitHub API request to {path} failed with status {response.status_code}: "
            f"{response.text}"
        )
    return response.json()


class GitHubOAuthProvider(IOAuthProvider):
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def get_authorization_url(self, state: str) -> str:
        params = httpx.QueryParams(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "scope": _SCOPE,
                "state": state,
            }
        )
        return f"{_AUTHORIZE_URL}?{params}"

    async def exchange_code_for_token(self, code: str) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                },
            )
        body = response.json()

        if response.is_error or "error" in body:
            reason = body.get("error_description", body.get("error", response.text))
            raise GitHubApiError(f"GitHub token exchange failed: {reason}")

        access_token = body.get("access_token")
        if not access_token:
            raise GitHubApiError("GitHub token exchange response had no access_token.")
        return str(access_token)

    async def fetch_user_profile(self, access_token: str) -> OAuthUserProfile:
        data = await _github_get("/user", access_token)
        return OAuthUserProfile(
            provider_user_id=str(data["id"]),
            email=data.get("email"),
            # `login` (the @handle) over the optional display `name`: it's
            # always present, and what "Connect GitHub" needs to show as
            # "Connected as @login" - unlike a future login-via-GitHub use
            # case, which would want the display name for User.full_name.
            name=data.get("login") or data.get("name"),
        )

    async def list_repositories(self, access_token: str) -> list[RepositoryInfo]:
        data = await _github_get(
            "/user/repos",
            access_token,
            params={"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator"},
        )
        return [
            RepositoryInfo(
                provider_repo_id=str(repo["id"]),
                owner=repo["owner"]["login"],
                name=repo["name"],
                full_name=repo["full_name"],
                private=repo["private"],
                default_branch=repo["default_branch"],
                html_url=repo["html_url"],
            )
            for repo in data
        ]


class GitHubVersionControlProvider(IVersionControlProvider):
    """A real, working `IVersionControlProvider` — used by Phase 7's
    deterministic impact analysis to read a pull request's changed file
    paths. Stateless: unlike `GitHubOAuthProvider`, it needs no OAuth App
    credentials, just a per-call (optional) access token.
    """

    async def get_diff(self, repository: str, ref: str) -> Any:
        raise NotImplementedYetError(
            "Reading full diff content is not implemented yet - impact analysis "
            "only needs changed file paths (see list_changed_files)."
        )

    async def list_changed_files(
        self, owner: str, repo: str, pull_number: int, access_token: str | None = None
    ) -> list[ChangedFile]:
        # A single page (up to 100 files) - matches this codebase's existing
        # list_repositories precedent of not exhaustively paginating GitHub
        # list endpoints. See ADR 0008 for the documented limitation.
        data = await _github_get(
            f"/repos/{owner}/{repo}/pulls/{pull_number}/files",
            access_token,
            params={"per_page": 100},
        )
        return [
            ChangedFile(
                path=item["filename"],
                status=item["status"],
                previous_path=item.get("previous_filename"),
            )
            for item in data
        ]
