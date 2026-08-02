"""Graph Validation Dashboard RFC — `run_parity_check` against a real
indexed repository: real git clone, real parse, real Neo4j write, real
Postgres persistence. Proves the service wires `get_full_graph` +
`materialize_repository_graph` + `compare_graphs` together correctly and
reports PASS for a freshly-indexed repository (single-repo materialization
is lossless per RFC-06/RFC-06's own replay test; the one additive
`confidence` edge property is excluded by `DEFAULT_IGNORE_RULES`, not a
byte-match requirement).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.services.indexing_service import index_repository
from app.knowledge_engine.parity.report import OverallResult
from app.models.repository import Repository
from app.models.user import User
from app.services.parity_service import run_parity_check

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def repository_row(db_session: AsyncSession) -> Repository:
    user = User(email=f"test-{uuid.uuid4()}@example.com", full_name="Test User")
    db_session.add(user)
    await db_session.flush()

    repo = Repository(
        user_id=user.id,
        owner="test-owner",
        name="spring-boot-repo",
        full_name="test-owner/spring-boot-repo",
        html_url="https://github.com/test-owner/spring-boot-repo",
        default_branch="main",
        source="github",
        github_repo_id=str(uuid.uuid4().int)[:10],
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


@pytest.fixture
async def graph_repository(
    repository_row: Repository,
) -> AsyncGenerator[Neo4jGraphRepository, None]:
    repo = Neo4jGraphRepository(get_driver())
    yield repo
    await repo.replace_repository_graph(str(repository_row.id), GraphPayload())


async def test_parity_check_passes_for_a_freshly_indexed_repository(
    spring_boot_git_repo: Path,
    repository_row: Repository,
    graph_repository: Neo4jGraphRepository,
    db_session: AsyncSession,
) -> None:
    repository_id = repository_row.id
    await index_repository(
        repository_id=str(repository_id),
        html_url=str(spring_boot_git_repo),
        ref="main",
        db=db_session,
    )

    report = await run_parity_check(db_session, graph_repository, repository_id)

    assert report.overall_result == OverallResult.PASS
    assert report.node_statistics.legacy_count > 0
    assert report.node_statistics.legacy_count == report.node_statistics.matched_count
    assert report.similarity_percentage == 100.0
    assert report.missing_nodes == ()
    assert report.missing_edges == ()


async def test_parity_check_reports_missing_everything_for_an_unindexed_repository(
    repository_row: Repository,
    graph_repository: Neo4jGraphRepository,
    db_session: AsyncSession,
) -> None:
    """No indexing run has happened -- both sides are empty, which is a
    trivial PASS (nothing to disagree about), not a crash."""
    report = await run_parity_check(db_session, graph_repository, repository_row.id)

    assert report.overall_result == OverallResult.PASS
    assert report.node_statistics.legacy_count == 0
    assert report.node_statistics.materialized_count == 0
