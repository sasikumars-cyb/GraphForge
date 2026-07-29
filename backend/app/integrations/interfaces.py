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

    @abstractmethod
    async def get_file_content(
        self, owner: str, repo: str, path: str, access_token: str | None = None
    ) -> str | None:
        """Raw content of `path` at the repository's default branch, or
        `None` if it doesn't exist. Used only by the CODEOWNERS fallback
        in `ReadGitHistoryTool` (`app.ai.agent.tools`) - a missing file is
        the normal case here, never an error."""
        raise NotImplementedError


class IGitWriteProvider(ABC):
    """Port for git *write* operations: branch creation, atomic commits,
    pull requests, and posting a PR comment.

    Deliberately a separate interface from `IVersionControlProvider`
    (read-only diffs/history/content), not a superset of it: not every VCS
    backend registered in this app needs to support writes.
    `LocalGitVersionControlProvider` (`app.integrations.local_git`) is an
    intentionally read-only demo backend for PR impact analysis against a
    local clone — it has no concept of opening a pull request against
    itself, and forcing it to implement these methods (even as
    `NotImplementedError` stubs) would be pure ceremony. A backend that
    *does* support writes (GitHub today; a future GitLab/Bitbucket/Azure
    DevOps adapter) implements both interfaces.

    `create_git_write_provider()` (`app.integrations.factory`) is the one
    place that decides which concrete backend answers this — the
    execution agents (create_branch, commit_changes, create_pull_request,
    run_tests) and the PR-comment-posting router call the factory, never
    `GitHubVersionControlProvider` by name.
    """

    @abstractmethod
    async def get_branch_sha(
        self, owner: str, repo: str, branch: str, access_token: str | None = None
    ) -> str:
        """Return the HEAD SHA for `branch`. Raises on 404 or failure."""
        raise NotImplementedError

    @abstractmethod
    async def get_branch_sha_or_none(
        self, owner: str, repo: str, branch: str, access_token: str | None = None
    ) -> str | None:
        """Like `get_branch_sha` but returns None on 404 instead of raising."""
        raise NotImplementedError

    @abstractmethod
    async def create_branch(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        from_sha: str,
        access_token: str | None = None,
    ) -> str:
        """Create a new branch ref from `from_sha`. Returns the new ref's SHA."""
        raise NotImplementedError

    @abstractmethod
    async def create_commit(
        self,
        owner: str,
        repo: str,
        branch: str,
        files: list[dict[str, str]],
        message: str,
        access_token: str | None = None,
    ) -> str:
        """Create a single atomic commit on `branch`. Returns the new commit SHA."""
        raise NotImplementedError

    @abstractmethod
    async def get_commit(
        self, owner: str, repo: str, sha: str, access_token: str | None = None
    ) -> dict[str, Any]:
        """Fetch a commit by SHA — used for idempotency checks."""
        raise NotImplementedError

    @abstractmethod
    async def get_check_runs(
        self, owner: str, repo: str, ref: str, access_token: str | None = None
    ) -> list[dict[str, Any]]:
        """CI check runs for a ref (SHA or branch name); empty if none."""
        raise NotImplementedError

    @abstractmethod
    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Open a pull request. Returns the raw provider PR object."""
        raise NotImplementedError

    @abstractmethod
    async def get_pull_request_by_head(
        self, owner: str, repo: str, head_branch: str, access_token: str | None = None
    ) -> dict[str, Any] | None:
        """Find an existing PR for `head_branch`, or None — idempotency
        lookup for create_pull_request's race-recovery path."""
        raise NotImplementedError

    @abstractmethod
    async def post_pull_request_comment(
        self, owner: str, repo: str, pull_number: int, body: str, access_token: str | None = None
    ) -> Any:
        """Post `body` as a new comment on the PR's conversation."""
        raise NotImplementedError


class IIssueTrackerProvider(ABC):
    """Port for reading and creating tracker issues.

    `Jira` (`app.tools.implementations.jira_tool.JiraTool`) is the first
    real implementation, added to satisfy this interface without changing
    its own existing behavior — see `JiraIssueTrackerAdapter` in the same
    module. The read methods (`get_issue`/`search_issues`/
    `build_context_text`) are what let the Planning Agent's Jira
    enrichment step become tracker-agnostic in principle: a future Linear/
    ServiceNow/Azure Boards/GitHub Issues adapter implementing this same
    interface would need no Planning Agent change, only its own adapter
    class and a registration entry (see Part 5 of the deterministic-logic
    task this interface was introduced for).
    """

    @abstractmethod
    async def create_issue(self, project_key: str, summary: str, description: str) -> Any:
        """Create an issue and return its identifier."""
        raise NotImplementedError

    @abstractmethod
    async def get_issue(self, issue_key: str) -> dict[str, Any] | None:
        """Fetch one issue's normalized fields by its tracker-specific key
        (e.g. a Jira issue key, a Linear identifier). None if not found or
        not configured."""
        raise NotImplementedError

    @abstractmethod
    def extract_issue_key(self, text: str) -> str | None:
        """Find this tracker's issue-key shape in free text (a pasted URL
        or bare key) — the first step of enrichment, before a lookup."""
        raise NotImplementedError

    @abstractmethod
    def build_context_text(self, issue: dict[str, Any]) -> str:
        """Render one fetched issue (`get_issue`'s return value) into the
        LLM-ready prompt text an enrichment step embeds — the same shape
        every tracker's issue reduces to, regardless of its own field
        names."""
        raise NotImplementedError
