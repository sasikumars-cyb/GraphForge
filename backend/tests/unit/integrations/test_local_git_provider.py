"""Real subprocess `git diff` against a real local repo - no mocking, same
philosophy as `tests/integration/conftest.py`'s `spring_boot_git_repo`."""

import subprocess
from pathlib import Path

import pytest

from app.integrations.local_git import LocalGitDiffError, LocalGitVersionControlProvider

_GIT_AUTHOR = ["-c", "user.email=test@example.com", "-c", "user.name=Test"]


def _git(repo_path: Path, *args: str) -> None:
    subprocess.run(["git", *_GIT_AUTHOR, *args], cwd=repo_path, check=True, capture_output=True)


@pytest.fixture
def demo_repo(tmp_path: Path) -> Path:
    """A real repo at `<clone_root>/order-service` with a `main` branch and
    a `pr-1` branch that adds, modifies, deletes, and renames files -
    exercising every status `list_changed_files` has to parse."""
    clone_root = tmp_path
    repo_path = clone_root / "order-service"
    repo_path.mkdir()

    (repo_path / "keep.txt").write_text("unchanged\n")
    (repo_path / "modify_me.txt").write_text("original\n")
    (repo_path / "delete_me.txt").write_text("bye\n")
    (repo_path / "old_name.txt").write_text("same content, new name\n")

    _git(repo_path, "init", "-q")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-q", "-m", "initial")
    _git(repo_path, "branch", "-m", "main")

    _git(repo_path, "checkout", "-q", "-b", "pr-1")
    (repo_path / "modify_me.txt").write_text("changed\n")
    (repo_path / "new_file.txt").write_text("brand new\n")
    (repo_path / "delete_me.txt").unlink()
    (repo_path / "old_name.txt").rename(repo_path / "new_name.txt")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-q", "-m", "pr-1 changes")

    return clone_root


@pytest.mark.asyncio
async def test_list_changed_files_reports_every_status(demo_repo: Path) -> None:
    provider = LocalGitVersionControlProvider(clone_root=demo_repo)

    changed = await provider.list_changed_files(owner="local", repo="order-service", pull_number=1)
    by_status = {c.path: c for c in changed}

    assert by_status["modify_me.txt"].status == "modified"
    assert by_status["new_file.txt"].status == "added"
    assert by_status["delete_me.txt"].status == "removed"
    assert by_status["new_name.txt"].status == "renamed"
    assert by_status["new_name.txt"].previous_path == "old_name.txt"
    assert "keep.txt" not in by_status


@pytest.mark.asyncio
async def test_list_changed_files_unknown_branch_raises(demo_repo: Path) -> None:
    provider = LocalGitVersionControlProvider(clone_root=demo_repo)

    with pytest.raises(LocalGitDiffError):
        await provider.list_changed_files(owner="local", repo="order-service", pull_number=999)


@pytest.mark.asyncio
async def test_get_diff_returns_unified_diff_content(demo_repo: Path) -> None:
    provider = LocalGitVersionControlProvider(clone_root=demo_repo)

    diff = await provider.get_diff(owner="local", repo="order-service", pull_number=1)

    assert "modify_me.txt" in diff
    assert "-original" in diff
    assert "+changed" in diff


@pytest.mark.asyncio
async def test_get_recent_file_authors_returns_the_commit_author(demo_repo: Path) -> None:
    provider = LocalGitVersionControlProvider(clone_root=demo_repo)

    authors = await provider.get_recent_file_authors(
        owner="local", repo="order-service", file_paths={"modify_me.txt", "new_file.txt"}
    )

    assert authors["modify_me.txt"] == ["Test"]
    assert authors["new_file.txt"] == ["Test"]


@pytest.mark.asyncio
async def test_get_file_content_returns_committed_content(demo_repo: Path) -> None:
    provider = LocalGitVersionControlProvider(clone_root=demo_repo)

    content = await provider.get_file_content(owner="local", repo="order-service", path="keep.txt")

    assert content == "unchanged\n"


@pytest.mark.asyncio
async def test_get_file_content_returns_none_for_missing_file(demo_repo: Path) -> None:
    provider = LocalGitVersionControlProvider(clone_root=demo_repo)

    content = await provider.get_file_content(
        owner="local", repo="order-service", path="CODEOWNERS"
    )

    assert content is None
