"""`clone_repository` against a real local git repo - real `git clone`
subprocess, real filesystem, no mocking."""

from pathlib import Path

import pytest

from app.indexer.scanner.repository_cloner import RepositoryCloneError, clone_repository

pytestmark = pytest.mark.asyncio


async def test_clones_repository_at_ref(spring_boot_git_repo: Path) -> None:
    async with clone_repository(str(spring_boot_git_repo), "main") as clone_path:
        assert (clone_path / "pom.xml").is_file()
        assert (clone_path / "src/main/java/com/example/orders/OrderController.java").is_file()


async def test_clone_directory_is_removed_after_context_exit(spring_boot_git_repo: Path) -> None:
    captured_path: Path | None = None
    async with clone_repository(str(spring_boot_git_repo), "main") as clone_path:
        captured_path = clone_path
        assert captured_path.exists()

    assert not captured_path.exists()


async def test_clone_directory_is_removed_even_on_failure(spring_boot_git_repo: Path) -> None:
    captured_path: Path | None = None
    with pytest.raises(RuntimeError):
        async with clone_repository(str(spring_boot_git_repo), "main") as clone_path:
            captured_path = clone_path
            raise RuntimeError("boom")

    assert captured_path is not None
    assert not captured_path.exists()


async def test_nonexistent_source_raises_clone_error(tmp_path: Path) -> None:
    missing_source = tmp_path / "does-not-exist"

    with pytest.raises(RepositoryCloneError):
        async with clone_repository(str(missing_source), "main"):
            pass


async def test_nonexistent_ref_raises_clone_error(spring_boot_git_repo: Path) -> None:
    with pytest.raises(RepositoryCloneError):
        async with clone_repository(str(spring_boot_git_repo), "no-such-branch"):
            pass
