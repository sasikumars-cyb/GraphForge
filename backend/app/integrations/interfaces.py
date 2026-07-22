"""Contracts for external systems.

Defining these before an adapter exists is what lets a GitHub client and a
Jira client be added as pure additions to this package, with no change to
any service that depends on the interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OAuthUserProfile:
    """The subset of a provider's user profile needed to create/link a
    local User account. GitHub's actual profile payload has many more
    fields; a real adapter maps whatever it needs down to this shape.
    """

    provider_user_id: str
    email: str | None
    name: str | None


@dataclass(frozen=True)
class RepositoryInfo:
    """The subset of a provider's repository payload needed to let a user
    pick which repositories to track."""

    provider_repo_id: str
    owner: str
    name: str
    full_name: str
    private: bool
    default_branch: str
    html_url: str


class IOAuthProvider(ABC):
    """Port for an OAuth-based provider (e.g. GitHub) — both the
    login-identity use case and the "connect my account for repo access"
    use case go through this same contract.

    `GitHubOAuthProvider` (app.integrations.github) is a real, working
    implementation used today by `/api/v1/github/connect` and
    `/api/v1/github/callback` (repo-access "Connect GitHub", not login).

    `api/v1/routers/oauth.py`'s `/auth/github/login` / `/auth/github/callback`
    routes are a *separate*, still-unimplemented use case (signing in AS a
    GitHub identity) and are deliberately not wired to this provider yet —
    see ADR 0006.
    """

    @abstractmethod
    def get_authorization_url(self, state: str) -> str:
        """Return the URL to redirect the user to for consent."""
        raise NotImplementedError

    @abstractmethod
    async def exchange_code_for_token(self, code: str) -> str:
        """Exchange an authorization code for a provider access token."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_user_profile(self, access_token: str) -> OAuthUserProfile:
        """Fetch the authenticated user's profile from the provider."""
        raise NotImplementedError

    @abstractmethod
    async def list_repositories(self, access_token: str) -> list[RepositoryInfo]:
        """List repositories the token's owner has access to."""
        raise NotImplementedError


@dataclass(frozen=True)
class ChangedFile:
    """One file changed by a pull request — enough to map it to an indexed
    graph node by `file_path`, without needing the diff content itself
    (Phase 7's impact analysis is deterministic and file-based, not a
    content/AST diff — see ADR 0008)."""

    path: str
    status: str
    previous_path: str | None = None


class IVersionControlProvider(ABC):
    """Port for fetching repository content, change diffs, and file
    history.

    `list_changed_files` is real, working (Phase 7 - deterministic impact
    analysis). `get_diff` and `get_recent_file_authors` back the Change
    Investigation Agent's optional evidence-gathering tools (see
    `app.ai.agent`) - the agent decides whether to call them at all, so
    neither is invoked by the deterministic engine or the original
    single-shot AI analysis path.
    """

    @abstractmethod
    async def get_diff(
        self, owner: str, repo: str, pull_number: int, access_token: str | None = None
    ) -> str:
        """Return the unified diff for a pull request's changes."""
        raise NotImplementedError

    @abstractmethod
    async def list_changed_files(
        self, owner: str, repo: str, pull_number: int, access_token: str | None = None
    ) -> list[ChangedFile]:
        """List the files changed by a pull request. `access_token` may be
        omitted for public repositories (subject to the provider's
        unauthenticated rate limits)."""
        raise NotImplementedError

    @abstractmethod
    async def get_recent_file_authors(
        self, owner: str, repo: str, file_paths: set[str], access_token: str | None = None
    ) -> dict[str, list[str]]:
        """For each of `file_paths`, the logins/names of whoever most
        recently committed to it - real authorship data to ground
        reviewer suggestions in, instead of the model guessing a name."""
        raise NotImplementedError


class IIssueTrackerProvider(ABC):
    """Port for reading and creating tracker issues.

    Future implementation: a Jira adapter (not built yet).
    """

    @abstractmethod
    async def create_issue(self, project_key: str, summary: str, description: str) -> Any:
        """Create an issue and return its identifier."""
        raise NotImplementedError
