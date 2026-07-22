"""A local-filesystem `IVersionControlProvider`, for demoing ChangeGuard
against repositories that only exist on disk (no GitHub involved at all).

Not used in production - see `app.integrations.factory`, which only
constructs this when `Settings.vcs_provider == "local_git"`, an explicit
opt-in for local demo environments.
"""

import subprocess
from pathlib import Path

from app.core.exceptions import AppError
from app.integrations.interfaces import ChangedFile, IVersionControlProvider

# GitHub's `pulls/{n}/files` response uses these words; git's
# `--name-status` uses single letters. Mapping to GitHub's vocabulary keeps
# `ChangedFile.status` meaning the same thing regardless of which provider
# produced it.
_STATUS_LETTERS = {
    "A": "added",
    "M": "modified",
    "D": "removed",
    "R": "renamed",
    "C": "copied",
}


class LocalGitDiffError(AppError):
    """Raised when `git diff` against a local demo repository fails."""

    status_code = 500
    error_code = "local_git_diff_failed"


class LocalGitVersionControlProvider(IVersionControlProvider):
    """Resolves a pull request's changed files via a local `git diff`.

    A "pull request" here is just a branch: `base_ref` is always `main`,
    and `head_ref` is `pr-{pull_number}` - a convention this demo's seed
    script fully controls (it creates both the branches and the
    `PullRequest.number` values that name them). `owner`/`access_token` are
    accepted to satisfy `IVersionControlProvider` but unused - there's no
    multi-owner or auth concept for a directory on disk.
    """

    def __init__(self, clone_root: Path) -> None:
        self._clone_root = clone_root

    async def get_diff(
        self, owner: str, repo: str, pull_number: int, access_token: str | None = None
    ) -> str:
        repo_dir = self._clone_root / repo
        head_ref = f"pr-{pull_number}"
        try:
            result = subprocess.run(
                ["git", "diff", "-M", "main", head_ref],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            raise LocalGitDiffError(
                f"git diff failed for {repo} ({head_ref} vs main): {exc.stderr}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LocalGitDiffError(f"git diff timed out for {repo} ({head_ref})") from exc
        return result.stdout

    async def get_recent_file_authors(
        self, owner: str, repo: str, file_paths: set[str], access_token: str | None = None
    ) -> dict[str, list[str]]:
        repo_dir = self._clone_root / repo
        authors_by_path: dict[str, list[str]] = {}
        for path in file_paths:
            try:
                result = subprocess.run(
                    ["git", "log", "--format=%an", "-n", "5", "--", path],
                    cwd=repo_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                authors_by_path[path] = []
                continue
            seen: dict[str, None] = {}
            for name in result.stdout.splitlines():
                if name:
                    seen.setdefault(name, None)
            authors_by_path[path] = list(seen)[:3]
        return authors_by_path

    async def list_changed_files(
        self, owner: str, repo: str, pull_number: int, access_token: str | None = None
    ) -> list[ChangedFile]:
        repo_dir = self._clone_root / repo
        head_ref = f"pr-{pull_number}"
        try:
            result = subprocess.run(
                ["git", "diff", "--name-status", "-M", "main", head_ref],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            raise LocalGitDiffError(
                f"git diff failed for {repo} ({head_ref} vs main): {exc.stderr}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LocalGitDiffError(f"git diff timed out for {repo} ({head_ref})") from exc

        return [_parse_status_line(line) for line in result.stdout.splitlines() if line]


def _parse_status_line(line: str) -> ChangedFile:
    fields = line.split("\t")
    code, rest = fields[0], fields[1:]
    status = _STATUS_LETTERS.get(code[0], code)

    if code.startswith(("R", "C")) and len(rest) == 2:
        previous_path, path = rest
        return ChangedFile(path=path, status=status, previous_path=previous_path)

    return ChangedFile(path=rest[0], status=status)
