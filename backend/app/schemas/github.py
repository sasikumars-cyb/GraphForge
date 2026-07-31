"""Request/response schemas for the GitHub connect/list/select flow and
persisted repository/PR data.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GitHubConnectionStatus(BaseModel):
    connected: bool
    github_username: str | None = None
    connected_at: datetime | None = None
    # "oauth" | "pat" | None (not connected). See
    # app.models.github_connection.GitHubConnection.auth_method.
    auth_method: str | None = None
    # Set only just after a PAT connect whose token is missing the `repo`
    # scope - advisory, never blocks the connection. See
    # app.services.github_service.connect_with_pat.
    scope_warning: str | None = None


class GitHubConnectAuthorizationUrl(BaseModel):
    authorization_url: str


class GitHubPATConnectRequest(BaseModel):
    """Body for POST /github/connection/pat - the PAT-based alternative to
    the OAuth /connect + /callback round trip."""

    token: str


class AvailableRepository(BaseModel):
    """One repo from the user's live GitHub list, cross-referenced against
    what's already tracked."""

    provider_repo_id: str
    owner: str
    name: str
    full_name: str
    private: bool
    default_branch: str
    html_url: str
    is_selected: bool


class RepositorySelection(BaseModel):
    """One repo the user is choosing to track — the full metadata is
    already known from the `AvailableRepository` list, so the client sends
    it back rather than us re-fetching it from GitHub."""

    provider_repo_id: str
    owner: str
    name: str
    full_name: str
    private: bool
    default_branch: str
    html_url: str


class RepositorySelectionRequest(BaseModel):
    """The full desired set of tracked repositories — replaces whatever was
    previously selected (submitting an empty list untracks everything)."""

    repositories: list[RepositorySelection] = Field(default_factory=list)


class RepositoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    github_repo_id: str
    # "github" | "local" — see app.models.repository.Repository.source.
    source: str
    owner: str
    name: str
    full_name: str
    private: bool
    default_branch: str
    html_url: str
    created_at: datetime


class LocalRepositoryCreateRequest(BaseModel):
    """Body for POST /repositories/local — registers one folder (resolved
    against the server's configured LOCAL_REPOS_ROOT) as a tracked,
    indexable repository. See app.services.local_repository_service.
    The branch indexed is always auto-detected from the folder's current
    git checkout — no user override, to keep the form to two fields."""

    name: str
    # Relative to LOCAL_REPOS_ROOT — never an absolute host path, see
    # local_repository_service's traversal guard.
    path: str


class PullRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int
    title: str
    state: str
    is_draft: bool
    author_login: str
    html_url: str
    head_ref: str
    base_ref: str
    github_created_at: datetime
    github_updated_at: datetime
