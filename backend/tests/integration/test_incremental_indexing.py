"""`run_indexing`'s KAN-32 incremental decision path, end to end: real
Postgres (`db_session`), real Neo4j, a real local-git full index to
bootstrap `last_indexed_commit_sha`/`last_indexed_language`, then a mocked
GitHub API (`compute_changed_files`/`resolve_branch_head_sha`/
`materialize_changed_files`, patched on `indexing_service` where they're
imported — same seam `test_cross_repo_linker.py`'s `patch.object(
indexing_service, "index_repository", ...)` already uses) for the
incremental leg itself, since that leg never touches git at all by
design.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.scanner.incremental import ChangedFile
from app.indexer.services import indexing_service
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def _make_user(db: AsyncSession) -> User:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com", full_name="Test User")
    db.add(user)
    await db.flush()
    return user


async def _make_repository(db: AsyncSession, user: User, html_url: str) -> Repository:
    repo = Repository(
        id=uuid.uuid4(),
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner="acme",
        name="widgets",
        full_name="acme/widgets",
        default_branch="main",
        html_url=html_url,
    )
    db.add(repo)
    await db.flush()
    return repo


@pytest.fixture
async def graph_repository() -> AsyncGenerator[Neo4jGraphRepository, None]:
    yield Neo4jGraphRepository(get_driver())


async def test_first_index_is_always_full_and_records_tracking_fields(
    db_session: AsyncSession, spring_boot_git_repo: Path, graph_repository: Neo4jGraphRepository
) -> None:
    """No `last_indexed_commit_sha` yet — must go straight to a full index,
    no GitHub API calls attempted at all (nothing mocked here; a real call
    would fail loudly against `spring_boot_git_repo`'s local path)."""
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(spring_boot_git_repo))

    try:
        summary = await indexing_service.run_indexing(db_session, repo)

        assert summary["controllers"] == 1
        assert "files_reindexed" not in summary  # a full index, not a scoped one
        assert repo.last_indexed_commit_sha is not None
        assert len(repo.last_indexed_commit_sha) == 40
        assert repo.last_indexed_language == "java-spring-boot"
        assert repo.last_indexed_at is not None
    finally:
        await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_safe_diff_runs_incrementally_and_updates_only_that_file(
    db_session: AsyncSession, spring_boot_git_repo: Path, graph_repository: Neo4jGraphRepository
) -> None:
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(spring_boot_git_repo))

    try:
        await indexing_service.run_indexing(db_session, repo)
        first_commit = repo.last_indexed_commit_sha
        first_graph = await graph_repository.get_full_graph(str(repo.id))
        first_node_ids = {n.id for n in first_graph.nodes}

        changed = [
            ChangedFile(
                path="src/main/java/com/example/orders/OrderController.java", status="modified"
            )
        ]

        with (
            patch.object(
                indexing_service,
                "resolve_branch_head_sha",
                new=AsyncMock(return_value="new-head-sha"),
            ),
            patch.object(
                indexing_service, "compute_changed_files", new=AsyncMock(return_value=changed)
            ),
            patch.object(indexing_service, "materialize_changed_files") as mock_materialize,
        ):
            # Re-serve the same real file content from the bootstrap repo —
            # proves the scoped re-parse actually runs the real parser
            # against real content, not a canned model.
            real_file = spring_boot_git_repo / changed[0].path

            class _FakeWorkDir:
                async def __aenter__(self) -> Path:
                    work_dir = spring_boot_git_repo.parent / f"incremental-{uuid.uuid4()}"
                    target = work_dir / changed[0].path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(real_file.read_bytes())
                    self._work_dir = work_dir
                    return work_dir

                async def __aexit__(self, *exc: object) -> None:
                    import shutil

                    shutil.rmtree(self._work_dir, ignore_errors=True)

            mock_materialize.return_value = _FakeWorkDir()

            summary = await indexing_service.run_indexing(db_session, repo)

        assert summary["files_reindexed"] == 1
        assert repo.last_indexed_commit_sha == "new-head-sha"
        assert repo.last_indexed_commit_sha != first_commit

        graph = await graph_repository.get_full_graph(str(repo.id))
        node_ids = {n.id for n in graph.nodes}
        # Everything from the untouched files (FeignClient, KafkaTopic,
        # MavenDependency, ...) survives the scoped update unchanged.
        assert node_ids >= (first_node_ids - {n for n in first_node_ids if "controller" in n})
    finally:
        await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_unsafe_diff_falls_back_to_a_full_index(
    db_session: AsyncSession, spring_boot_git_repo: Path, graph_repository: Neo4jGraphRepository
) -> None:
    """A changed pom.xml makes the diff unsafe (see
    is_safe_for_incremental_update) — must fall back to the real,
    unmocked full `index_repository` path rather than attempt a scoped
    update with a mocked materializer that would never even be called."""
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(spring_boot_git_repo))

    try:
        await indexing_service.run_indexing(db_session, repo)

        with (
            patch.object(
                indexing_service,
                "resolve_branch_head_sha",
                new=AsyncMock(return_value="new-head-sha"),
            ),
            patch.object(
                indexing_service,
                "compute_changed_files",
                new=AsyncMock(return_value=[ChangedFile(path="pom.xml", status="modified")]),
            ),
        ):
            summary = await indexing_service.run_indexing(db_session, repo)

        # Fell back to the real full index against spring_boot_git_repo —
        # its own real HEAD sha, not the mocked "new-head-sha" (that mock
        # only feeds _attempt_incremental_index, which declined to run).
        assert "files_reindexed" not in summary
        assert summary["controllers"] == 1
        assert repo.last_indexed_commit_sha != "new-head-sha"
    finally:
        await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_uncomputable_diff_falls_back_to_a_full_index(
    db_session: AsyncSession, spring_boot_git_repo: Path, graph_repository: Neo4jGraphRepository
) -> None:
    """compute_changed_files returning None (GitHub API failure, a
    force-pushed history, ...) must fall back exactly like an unsafe diff
    does — never raise, never skip indexing."""
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(spring_boot_git_repo))

    try:
        await indexing_service.run_indexing(db_session, repo)

        with (
            patch.object(
                indexing_service,
                "resolve_branch_head_sha",
                new=AsyncMock(return_value="new-head-sha"),
            ),
            patch.object(
                indexing_service, "compute_changed_files", new=AsyncMock(return_value=None)
            ),
        ):
            summary = await indexing_service.run_indexing(db_session, repo)

        assert "files_reindexed" not in summary
        assert summary["controllers"] == 1
    finally:
        await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_local_source_repository_never_attempts_incremental(
    db_session: AsyncSession, spring_boot_git_repo: Path, graph_repository: Neo4jGraphRepository
) -> None:
    """A source="local" repository's html_url is a filesystem path, not a
    GitHub URL — resolve_branch_head_sha/compute_changed_files must never
    even be called for it (they'd have nothing valid to resolve against)."""
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(spring_boot_git_repo))
    repo.source = "local"
    await db_session.flush()

    try:
        with (
            patch.object(indexing_service, "resolve_branch_head_sha") as mock_resolve,
            patch.object(indexing_service, "compute_changed_files") as mock_compute,
        ):
            summary = await indexing_service.run_indexing(db_session, repo)

        # The GitHub-API functions are never even called for a local repo —
        # `resolve_head_commit_sha` (plain `git rev-parse HEAD` against the
        # clone `index_repository` already made) is what actually sets
        # `last_indexed_commit_sha` here, not either of these.
        mock_resolve.assert_not_called()
        mock_compute.assert_not_called()
        assert summary["controllers"] == 1
        assert repo.last_indexed_commit_sha is not None
    finally:
        await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())
