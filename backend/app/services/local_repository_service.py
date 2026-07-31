"""Register a local filesystem folder as a trackable, indexable
`Repository` — the non-GitHub counterpart to `github_service.
set_selected_repositories`.

Indexing itself needs no new code at all: `indexer.scanner.repository_
cloner.clone_repository()` already works against a local path unchanged
(`git clone <path> <dest>` is valid git syntax, and `_authenticated_url()`
only ever injects a token for `https://` URLs) - this module's entire job
is safely turning a user-submitted folder into a `Repository` row with a
resolved, validated `html_url`, nothing more.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError, ConflictError
from app.models.repository import Repository
from app.models.user import User

_GIT_TIMEOUT_SECONDS = 10


class LocalReposNotConfiguredError(AppError):
    """Raised when `LOCAL_REPOS_ROOT` isn't set - the feature is disabled
    entirely rather than silently resolving against some implicit default,
    since that default would otherwise be "the backend's own filesystem
    root," which must never be exposed by accident."""

    status_code = 503
    error_code = "local_repos_not_configured"


class InvalidLocalRepositoryPathError(AppError):
    """Raised for any path that fails validation - outside the configured
    root, missing, not a directory, or not a real git repository. Always
    422 (a precondition on the request), never leaks the resolved
    filesystem path in the message (only the path the user actually typed)
    so an error response can't be used to probe the container's
    filesystem layout."""

    status_code = 422
    error_code = "invalid_local_repository_path"


def _resolve_within_root(root: Path, user_path: str) -> Path:
    """Resolve `user_path` against `root` and prove the result is still a
    descendant of `root` - the actual security boundary. `user_path`
    containing `..` or being absolute is rejected up front as a fast,
    readable-error path; the `is_relative_to` check below is what
    actually matters (it also catches a symlink inside `root` that
    escapes it, which the up-front string check cannot)."""
    stripped = user_path.strip().strip("/")
    if not stripped or ".." in Path(stripped).parts:
        raise InvalidLocalRepositoryPathError(f"'{user_path}' is not a valid relative path.")

    resolved_root = root.resolve()
    candidate = (resolved_root / stripped).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise InvalidLocalRepositoryPathError(
            f"'{user_path}' is outside the configured local repositories root."
        )
    return candidate


async def _detect_current_branch(repo_dir: Path) -> str:
    """`git rev-parse --abbrev-ref HEAD` — doubles as "is this actually a
    git repository" validation (a non-repo folder fails this cleanly with
    a non-zero exit) and default-branch detection in one call, mirroring
    how `repository_cloner.clone_repository` already shells out to git via
    `asyncio.create_subprocess_exec` rather than the blocking `subprocess`
    module."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repo_dir),
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=_GIT_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise InvalidLocalRepositoryPathError(
            "Timed out inspecting this folder as a git repository."
        ) from exc

    if process.returncode != 0:
        raise InvalidLocalRepositoryPathError(
            f"This folder is not a git repository: {stderr.decode(errors='replace').strip()}"
        )
    return stdout.decode().strip()


async def create_local_repository(
    db: AsyncSession,
    user: User,
    name: str,
    path: str,
) -> Repository:
    """Validates `path` (must resolve under `Settings.local_repos_root`
    and be a real git repository), auto-detects its currently checked-out
    branch, and inserts one `Repository` row (`source="local"`).

    Unlike `github_service.set_selected_repositories`, this is
    insert-one, not replace-the-whole-set — a user adding a local folder
    never touches their GitHub-sourced tracked repositories.
    """
    settings = get_settings()
    if not settings.local_repos_root:
        raise LocalReposNotConfiguredError(
            "Local repository indexing is not configured on this server "
            "(LOCAL_REPOS_ROOT is unset)."
        )

    name = name.strip()
    if not name:
        raise InvalidLocalRepositoryPathError("A repository name is required.")

    resolved_path = _resolve_within_root(Path(settings.local_repos_root), path)
    if not resolved_path.is_dir():
        raise InvalidLocalRepositoryPathError(f"'{path}' does not exist or is not a folder.")

    branch = await _detect_current_branch(resolved_path)
    if branch == "HEAD":
        # `--abbrev-ref HEAD` literally returns "HEAD" for a detached
        # checkout - not a usable branch name for the indexer's later
        # `git clone --branch HEAD`, which expects a real ref.
        raise InvalidLocalRepositoryPathError(
            "This folder's git checkout has no branch checked out (detached HEAD) — "
            "check out a branch before adding it."
        )

    repository = Repository(
        id=uuid.uuid4(),
        user_id=user.id,
        github_repo_id=f"local:{name}",
        source="local",
        owner="local",
        name=name,
        full_name=f"local/{name}",
        private=False,
        default_branch=branch,
        html_url=str(resolved_path),
    )
    db.add(repository)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(f"A repository named '{name}' is already tracked.") from None
    await db.refresh(repository)
    return repository
