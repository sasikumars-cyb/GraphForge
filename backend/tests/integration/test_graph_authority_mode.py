"""ADR 0018 activation — `Settings.graph_authority_mode` switching the
indexing pipeline between writing the deterministic builder's graph
directly ("shadow"/"shadow_compare", today's unchanged default) and
writing the Materializer's projection of Engineering Memory as the
authoritative graph ("authoritative"). Real Postgres, real Neo4j, real
local-git clones — no mocks in the core assertions; mocks are used only to
simulate the two explicit failure modes `_write_repository_graph` must
survive without corrupting the graph.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.services import indexing_service
from app.models.repository import Repository
from app.models.user import User

def _set_mode(monkeypatch: pytest.MonkeyPatch, mode: str | None) -> None:
    if mode is None:
        monkeypatch.delenv("GRAPH_AUTHORITY_MODE", raising=False)
    else:
        monkeypatch.setenv("GRAPH_AUTHORITY_MODE", mode)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    yield
    get_settings.cache_clear()


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
async def graph_repository(repository_id_holder: list) -> AsyncGenerator[Neo4jGraphRepository, None]:
    repo = Neo4jGraphRepository(get_driver())
    yield repo
    for repository_id in repository_id_holder:
        await repo.replace_repository_graph(repository_id, GraphPayload())


@pytest.fixture
def repository_id_holder() -> list:
    return []


def test_unknown_mode_falls_back_to_shadow_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "not-a-real-mode")
    assert indexing_service._graph_authority_mode() == "shadow_compare"


def test_default_mode_is_shadow_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, None)
    assert indexing_service._graph_authority_mode() == "shadow_compare"


@pytest.mark.asyncio
async def test_shadow_compare_mode_writes_builder_graph_without_confidence_property(
    db_session: AsyncSession,
    python_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    repository_id_holder: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unchanged default: today's behavior is preserved exactly - the
    graph written is the builder's own payload, which never carries a
    `confidence` property (only the Materializer adds one)."""
    _set_mode(monkeypatch, "shadow_compare")
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(python_git_repo))
    repository_id_holder.append(str(repo.id))

    await indexing_service.run_indexing(db_session, repo)

    graph = await graph_repository.get_full_graph(str(repo.id))
    assert graph.nodes  # something was written
    module_nodes = [n for n in graph.nodes if "Module" in n.labels]
    assert module_nodes
    assert "confidence" not in module_nodes[0].properties
    edges_with_confidence = [e for e in graph.edges if "confidence" in e.properties]
    assert edges_with_confidence == []


@pytest.mark.asyncio
async def test_authoritative_mode_writes_materialized_graph_with_confidence(
    db_session: AsyncSession,
    python_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    repository_id_holder: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The activation's core claim: in "authoritative" mode, the graph
    actually in Neo4j is the Materializer's projection of Engineering
    Memory, not the builder's direct output - proven by the one property
    only the Materializer ever attaches (`confidence`), present on edges
    after this run where it was absent under "shadow_compare" above."""
    _set_mode(monkeypatch, "authoritative")
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(python_git_repo))
    repository_id_holder.append(str(repo.id))

    await indexing_service.run_indexing(db_session, repo)

    graph = await graph_repository.get_full_graph(str(repo.id))
    assert graph.nodes
    assert graph.edges
    edges_with_confidence = [e for e in graph.edges if "confidence" in e.properties]
    assert edges_with_confidence, "materialized edges must carry a confidence property"


@pytest.mark.asyncio
async def test_authoritative_mode_never_takes_the_incremental_path(
    db_session: AsyncSession,
    python_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    repository_id_holder: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KAN-32's incremental path never persists to Engineering Memory, so
    "authoritative" mode must never take it - even when a prior index
    exists and an incremental update would otherwise be eligible."""
    _set_mode(monkeypatch, "shadow_compare")
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(python_git_repo))
    repository_id_holder.append(str(repo.id))

    # Bootstrap a prior index so `_attempt_incremental_index` would
    # otherwise be eligible to run.
    await indexing_service.run_indexing(db_session, repo)
    assert repo.last_indexed_commit_sha is not None

    _set_mode(monkeypatch, "authoritative")
    with patch.object(
        indexing_service, "_attempt_incremental_index", new=AsyncMock(return_value=None)
    ) as mock_attempt:
        await indexing_service.run_indexing(db_session, repo)
        mock_attempt.assert_not_called()


@pytest.mark.asyncio
async def test_materialization_exception_falls_back_to_builder_graph(
    db_session: AsyncSession,
    python_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    repository_id_holder: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Postgres error (or anything else) during materialization must not
    crash indexing or leave the repository with no graph - it must fall
    back to the builder's own, already-trustworthy payload."""
    _set_mode(monkeypatch, "authoritative")
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(python_git_repo))
    repository_id_holder.append(str(repo.id))

    with patch.object(
        indexing_service,
        "materialize_repository_graph",
        new=AsyncMock(side_effect=RuntimeError("simulated postgres failure")),
    ):
        await indexing_service.run_indexing(db_session, repo)

    graph = await graph_repository.get_full_graph(str(repo.id))
    assert graph.nodes, "fallback must still leave a usable graph, not an empty one"
    edges_with_confidence = [e for e in graph.edges if "confidence" in e.properties]
    assert edges_with_confidence == [], "the fallback graph is the builder's, not the Materializer's"


@pytest.mark.asyncio
async def test_empty_materialization_falls_back_to_builder_graph(
    db_session: AsyncSession,
    python_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    repository_id_holder: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A materialization that succeeds but returns nothing (e.g. shadow
    generation silently failed to persist) must never be trusted to wipe
    an otherwise-populated graph."""
    _set_mode(monkeypatch, "authoritative")
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(python_git_repo))
    repository_id_holder.append(str(repo.id))

    with patch.object(
        indexing_service,
        "materialize_repository_graph",
        new=AsyncMock(return_value=GraphPayload()),
    ):
        await indexing_service.run_indexing(db_session, repo)

    graph = await graph_repository.get_full_graph(str(repo.id))
    assert graph.nodes, "an empty materialization must never wipe the graph to nothing"
