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


class GitHubConnectAuthorizationUrl(BaseModel):
    authorization_url: str


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
    owner: str
    name: str
    full_name: str
    private: bool
    default_branch: str
    html_url: str
    created_at: datetime


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
