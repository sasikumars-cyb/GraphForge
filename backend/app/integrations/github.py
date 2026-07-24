"""A real, working GitHub OAuth adapter — implements `IOAuthProvider`.

Used today for "Connect GitHub" (repo access). NOT wired to `/auth/github/login`
(signing in AS a GitHub identity) — that remains a separate, unimplemented use
case; see ADR 0006.

Talks to real GitHub endpoints (`github.com`, `api.github.com`). This module
makes no assumption about *whose* GitHub OAuth App is configured — that's
`GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` in `core.config`, which must be the
user's own personal OAuth App, never a company-owned one.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.exceptions import AppError
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


@dataclass(frozen=True)
class PostedComment:
    """What GitHub returns after creating an Issues API comment - just
    enough to link back to it. Lives here, not in `interfaces.py`:
    `post_pull_request_comment` isn't part of `IVersionControlProvider`
    (see `GitHubVersionControlProvider.post_pull_request_comment`'s
    docstring for why), so this return type has no reason to be shared
    either."""

    id: int
    html_url: str


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

    Every `IVersionControlProvider` method here is read-only. The one
    exception is `post_pull_request_comment`, a real write added for
    publishing AI review results - it isn't part of the interface (see its
    own docstring), so it's the only place this class has a side effect.
    """

    async def get_diff(
        self, owner: str, repo: str, pull_number: int, access_token: str | None = None
    ) -> str:
        """Unlike every other call in this module, GitHub returns this as
        raw diff *text*, not JSON - requested via the `.diff` media type
        rather than the default `_API_HEADERS`, so this doesn't go through
        `_github_get`."""
        headers = {
            "Accept": "application/vnd.github.v3.diff",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}", headers=headers
            )
        if response.is_error:
            raise GitHubApiError(
                f"GitHub diff request for {owner}/{repo}#{pull_number} failed with status "
                f"{response.status_code}: {response.text}"
            )
        return response.text

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

    async def get_recent_file_authors(
        self, owner: str, repo: str, file_paths: set[str], access_token: str | None = None
    ) -> dict[str, list[str]]:
        """Up to 3 most recent distinct commit authors per file, via
        GitHub's commits API filtered by `path`. Real authorship, not a
        guess - grounds reviewer suggestions the same way the graph
        grounds impact claims."""
        authors_by_path: dict[str, list[str]] = {}
        for path in file_paths:
            commits = await _github_get(
                f"/repos/{owner}/{repo}/commits",
                access_token,
                params={"path": path, "per_page": 5},
            )
            seen: dict[str, None] = {}
            for commit in commits:
                author = (commit.get("author") or {}).get("login") or commit.get("commit", {}).get(
                    "author", {}
                ).get("name")
                if author:
                    seen.setdefault(author, None)
            authors_by_path[path] = list(seen)[:3]
        return authors_by_path

    async def get_file_content(
        self, owner: str, repo: str, path: str, access_token: str | None = None
    ) -> str | None:
        """Raw file content via GitHub's contents API, requested with the
        `.raw` media type so the body is the file itself rather than a
        base64-wrapped JSON envelope - like `get_diff`, this doesn't go
        through `_github_get` (which always `.json()`s the body and treats
        every error status as fatal; a 404 here is the normal "no
        CODEOWNERS file" case, not an error)."""
        headers = {
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_API_BASE}/repos/{owner}/{repo}/contents/{path}", headers=headers
            )
        if response.status_code == 404:
            return None
        if response.is_error:
            raise GitHubApiError(
                f"GitHub content request for {owner}/{repo}/{path} failed with status "
                f"{response.status_code}: {response.text}"
            )
        return response.text

    async def post_pull_request_comment(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        body: str,
        access_token: str | None = None,
    ) -> PostedComment:
        """Posts `body` as a new comment on the PR's conversation tab, via
        GitHub's Issues Comment API - pull requests *are* issues for
        commenting purposes: `POST /repos/{owner}/{repo}/issues/{pull_number}/comments`.
        A real write, unlike every other method on this class - like
        `GitHubOAuthProvider.exchange_code_for_token`, this doesn't go
        through `_github_get` (GET-only, and always `.json()`s the body)."""
        headers = dict(_API_HEADERS)
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{_API_BASE}/repos/{owner}/{repo}/issues/{pull_number}/comments",
                headers=headers,
                json={"body": body},
            )
        if response.is_error:
            raise GitHubApiError(
                f"GitHub comment post to {owner}/{repo}#{pull_number} failed with status "
                f"{response.status_code}: {response.text}"
            )
        data = response.json()
        return PostedComment(id=data["id"], html_url=data["html_url"])

    # ------------------------------------------------------------------
    # Write methods — NOT part of IVersionControlProvider. Used by the
    # create_branch and commit_changes execution agents (Phase 5 PR #4).
    # ------------------------------------------------------------------

    async def get_branch_sha(
        self,
        owner: str,
        repo: str,
        branch: str,
        access_token: str | None = None,
    ) -> str:
        """Return the HEAD SHA for a branch. Raises GitHubApiError (502)
        on 404 or any other failure."""
        data = await _github_get(
            f"/repos/{owner}/{repo}/git/ref/heads/{branch}",
            access_token,
        )
        return str(data["object"]["sha"])

    async def get_branch_sha_or_none(
        self,
        owner: str,
        repo: str,
        branch: str,
        access_token: str | None = None,
    ) -> str | None:
        """Like get_branch_sha but returns None on 404 instead of raising."""
        headers = dict(_API_HEADERS)
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_API_BASE}/repos/{owner}/{repo}/git/ref/heads/{branch}",
                headers=headers,
            )
        if response.status_code == 404:
            return None
        if response.is_error:
            raise GitHubApiError(
                f"GitHub ref lookup for {owner}/{repo} heads/{branch} failed "
                f"with status {response.status_code}: {response.text}"
            )
        return str(response.json()["object"]["sha"])

    async def create_branch(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        from_sha: str,
        access_token: str | None = None,
    ) -> str:
        """Create a new branch ref. Returns the SHA of the new ref.
        Uses POST /repos/{owner}/{repo}/git/refs."""
        headers = dict(_API_HEADERS)
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{_API_BASE}/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch_name}", "sha": from_sha},
            )
        if response.is_error:
            raise GitHubApiError(
                f"GitHub branch creation for {owner}/{repo} '{branch_name}' failed "
                f"with status {response.status_code}: {response.text}"
            )
        return str(response.json()["object"]["sha"])

    async def create_commit(
        self,
        owner: str,
        repo: str,
        branch: str,
        files: list[dict[str, str]],
        message: str,
        access_token: str | None = None,
    ) -> str:
        """Create a single commit on `branch` with all `files` atomically
        via the Git Data API (trees + commits + ref update).

        Each file in `files` is {"path": "...", "content": "..."} for
        create/modify, or {"path": "...", "content": None} for delete.

        Returns the new commit SHA.
        """
        headers = dict(_API_HEADERS)
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        # 1. Get the current branch HEAD commit + its tree SHA
        branch_sha = await self.get_branch_sha(owner, repo, branch, access_token)
        commit_data = await _github_get(
            f"/repos/{owner}/{repo}/git/commits/{branch_sha}", access_token
        )
        base_tree_sha = commit_data["tree"]["sha"]

        # 2. Build tree entries
        tree_entries = []
        for f in files:
            if f.get("content") is None:
                # Delete: mode "100644", sha null
                tree_entries.append(
                    {"path": f["path"], "mode": "100644", "type": "blob", "sha": None}
                )
            else:
                tree_entries.append(
                    {"path": f["path"], "mode": "100644", "type": "blob", "content": f["content"]}
                )

        # 3. Create new tree
        async with httpx.AsyncClient() as client:
            tree_response = await client.post(
                f"{_API_BASE}/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={"base_tree": base_tree_sha, "tree": tree_entries},
            )
        if tree_response.is_error:
            raise GitHubApiError(
                f"GitHub tree creation failed with status "
                f"{tree_response.status_code}: {tree_response.text}"
            )
        new_tree_sha = tree_response.json()["sha"]

        # 4. Create commit
        async with httpx.AsyncClient() as client:
            commit_response = await client.post(
                f"{_API_BASE}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json={
                    "message": message,
                    "tree": new_tree_sha,
                    "parents": [branch_sha],
                },
            )
        if commit_response.is_error:
            raise GitHubApiError(
                f"GitHub commit creation failed with status "
                f"{commit_response.status_code}: {commit_response.text}"
            )
        new_commit_sha = commit_response.json()["sha"]

        # 5. Update branch ref to point to new commit
        async with httpx.AsyncClient() as client:
            ref_response = await client.patch(
                f"{_API_BASE}/repos/{owner}/{repo}/git/refs/heads/{branch}",
                headers=headers,
                json={"sha": new_commit_sha},
            )
        if ref_response.is_error:
            raise GitHubApiError(
                f"GitHub ref update failed with status "
                f"{ref_response.status_code}: {ref_response.text}"
            )

        return str(new_commit_sha)

    async def get_commit(
        self,
        owner: str,
        repo: str,
        sha: str,
        access_token: str | None = None,
    ) -> dict:
        """Fetch a commit by SHA. Used for idempotency checks."""
        return await _github_get(
            f"/repos/{owner}/{repo}/git/commits/{sha}", access_token
        )
