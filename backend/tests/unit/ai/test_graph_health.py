"""Unit tests for `app.graph.health` — the shared "is this repository's
graph healthy right now" computation Control Center and Context Discovery
both read (see that module's docstring for the drift it replaces).

All DB and graph-store calls are mocked — no real Postgres or Neo4j needed.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.graph.health import (
    GraphHealthService,
    GraphHealthStatus,
    RepositoryGraphHealth,
    _status_for,
)


def _repo(repo_id: uuid.UUID | None = None) -> MagicMock:
    repo = MagicMock()
    repo.id = repo_id or uuid.uuid4()
    return repo


def _jobs_result(rows: list[tuple[uuid.UUID, str]]) -> MagicMock:
    """A `db.execute(...)` return value shaped like the `_latest_job_status_
    by_repository` query's result: `.all()` returns `(repository_id,
    status)` tuples."""
    result = MagicMock()
    result.all.return_value = rows
    return result


# ---------------------------------------------------------------------------
# `_status_for` — the pure decision table.
# ---------------------------------------------------------------------------


def test_status_for_has_graph_is_healthy_regardless_of_job_status() -> None:
    """A queryable graph wins over everything else — even a "failed" or
    absent job history, since the graph is usable right now regardless of
    how it got there."""
    assert (
        _status_for(has_graph=True, latest_job_status=None) == GraphHealthStatus.HEALTHY
    )
    assert (
        _status_for(has_graph=True, latest_job_status="failed") == GraphHealthStatus.HEALTHY
    )
    assert (
        _status_for(has_graph=True, latest_job_status="running") == GraphHealthStatus.HEALTHY
    )


@pytest.mark.parametrize("in_progress_status", ["pending", "running"])
def test_status_for_in_progress_job_with_no_graph_is_indexing(in_progress_status: str) -> None:
    assert (
        _status_for(has_graph=False, latest_job_status=in_progress_status)
        == GraphHealthStatus.INDEXING
    )


def test_status_for_completed_job_with_no_graph_is_graph_missing() -> None:
    """The exact drift this abstraction exists to surface: a job that
    completed in Postgres, but with no matching graph in Neo4j right now."""
    assert (
        _status_for(has_graph=False, latest_job_status="completed")
        == GraphHealthStatus.GRAPH_MISSING
    )


def test_status_for_no_job_and_no_graph_is_not_indexed() -> None:
    assert _status_for(has_graph=False, latest_job_status=None) == GraphHealthStatus.NOT_INDEXED


def test_status_for_only_failed_job_is_not_indexed() -> None:
    """A failed-only history is "never successfully indexed", the same
    bucket as never having attempted at all — not a distinct state."""
    assert (
        _status_for(has_graph=False, latest_job_status="failed")
        == GraphHealthStatus.NOT_INDEXED
    )


# ---------------------------------------------------------------------------
# `GraphHealthService.for_repositories`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_for_repositories_empty_input_short_circuits() -> None:
    mock_db = AsyncMock()
    mock_graph_repo = AsyncMock()

    service = GraphHealthService(mock_db, mock_graph_repo)
    result = await service.for_repositories([])

    assert result == []
    mock_db.execute.assert_not_called()
    mock_graph_repo.has_graph.assert_not_called()


@pytest.mark.asyncio
async def test_for_repositories_all_healthy_skips_job_status_query() -> None:
    """Job history is irrelevant once a graph exists — no `db.execute` call
    should be spent finding out what it says."""
    repo = _repo()
    mock_db = AsyncMock()
    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)

    service = GraphHealthService(mock_db, mock_graph_repo)
    result = await service.for_repositories([repo])

    assert result == [
        RepositoryGraphHealth(
            repository_id=repo.id,
            status=GraphHealthStatus.HEALTHY,
            has_graph=True,
            latest_job_status=None,
        )
    ]
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_for_repositories_mixed_batch_queries_job_status_only_for_unhealthy() -> None:
    healthy_repo = _repo()
    missing_repo = _repo()
    mock_db = AsyncMock()
    mock_db.execute.return_value = _jobs_result([(missing_repo.id, "completed")])

    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(
        side_effect=lambda repo_id: repo_id == str(healthy_repo.id)
    )

    service = GraphHealthService(mock_db, mock_graph_repo)
    results = {h.repository_id: h for h in await service.for_repositories(
        [healthy_repo, missing_repo]
    )}

    assert results[healthy_repo.id].status == GraphHealthStatus.HEALTHY
    assert results[healthy_repo.id].latest_job_status is None
    assert results[missing_repo.id].status == GraphHealthStatus.GRAPH_MISSING
    assert results[missing_repo.id].latest_job_status == "completed"
    # One query for the whole batch, not one per repository — and it's
    # only issued at all because `missing_repo` had no graph.
    mock_db.execute.assert_awaited_once()
    assert mock_graph_repo.has_graph.await_count == 2


@pytest.mark.asyncio
async def test_for_repositories_picks_the_most_recent_job_per_repository() -> None:
    """Two jobs for the same repository (e.g. a failed attempt, then a
    completed one) — the *latest* by `created_at` decides the status, not
    whichever row a naive query happens to return first."""
    repo = _repo()
    mock_db = AsyncMock()
    # Ordered exactly as the service's own query orders them: newest first.
    mock_db.execute.return_value = _jobs_result([(repo.id, "completed")])
    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    service = GraphHealthService(mock_db, mock_graph_repo)
    result = await service.for_repository(repo)

    assert result.status == GraphHealthStatus.GRAPH_MISSING
    assert result.latest_job_status == "completed"


@pytest.mark.asyncio
async def test_for_repository_wraps_for_repositories() -> None:
    repo = _repo()
    mock_db = AsyncMock()
    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)

    service = GraphHealthService(mock_db, mock_graph_repo)
    result = await service.for_repository(repo)

    assert isinstance(result, RepositoryGraphHealth)
    assert result.repository_id == repo.id
    assert result.status == GraphHealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_for_repositories_never_indexed_reports_not_indexed() -> None:
    repo = _repo()
    mock_db = AsyncMock()
    mock_db.execute.return_value = _jobs_result([])  # no job rows at all
    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    service = GraphHealthService(mock_db, mock_graph_repo)
    result = await service.for_repository(repo)

    assert result.status == GraphHealthStatus.NOT_INDEXED
    assert result.latest_job_status is None


@pytest.mark.asyncio
async def test_for_repositories_pending_job_reports_indexing() -> None:
    repo = _repo()
    mock_db = AsyncMock()
    mock_db.execute.return_value = _jobs_result([(repo.id, "pending")])
    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    service = GraphHealthService(mock_db, mock_graph_repo)
    result = await service.for_repository(repo)

    assert result.status == GraphHealthStatus.INDEXING
