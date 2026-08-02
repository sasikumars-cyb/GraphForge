"""ADR 0018 RFC-04 — the full wired pipeline, end to end: real git clone,
real parse, real Neo4j graph write, real shadow reasoning (generate ->
validate -> confidence), real Postgres persistence via
`EngineeringMemoryService`. Proves `index_repository(..., db=...)` actually
persists — not just that each layer works in isolation.
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
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User

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


async def test_index_repository_with_db_persists_evidence_and_relationships(
    spring_boot_git_repo: Path,
    repository_row: Repository,
    graph_repository: Neo4jGraphRepository,
    db_session: AsyncSession,
) -> None:
    repository_id = str(repository_row.id)

    summary = await index_repository(
        repository_id=repository_id,
        html_url=str(spring_boot_git_repo),
        ref="main",
        db=db_session,
    )

    # The real, pre-existing indexing behavior is completely unaffected.
    assert summary["controllers"] == 1
    assert await graph_repository.has_graph(repository_id)

    # RFC-04's actual claim: Engineering Memory now holds what the shadow
    # pipeline produced.
    memory = EngineeringMemoryService(db_session)
    current_relationships = await memory.get_current_relationships(repository_row.id)
    assert len(current_relationships) > 0
    relationship_types = {r.relationship_type for r in current_relationships}
    assert "CONTAINS" in relationship_types

    # Every persisted relationship is confidence-scored, not just recorded.
    for record in current_relationships:
        assert record.confidence_state in {
            "verified",
            "highly_likely",
            "likely",
            "candidate",
            "rejected",
            "conflicting",
        }
        assert record.provenance  # non-empty, per ADR 0018's provenance invariant


async def test_index_repository_without_db_persists_nothing(
    spring_boot_git_repo: Path,
    repository_row: Repository,
    graph_repository: Neo4jGraphRepository,
    db_session: AsyncSession,
) -> None:
    """Backward compatibility, proven directly: omitting `db` (every
    pre-RFC-04 call site) must leave Engineering Memory completely empty,
    even though the reasoning pipeline still runs (generation, validation,
    confidence — all pure, no persistence)."""
    repository_id = str(repository_row.id)

    await index_repository(
        repository_id=repository_id, html_url=str(spring_boot_git_repo), ref="main"
    )

    memory = EngineeringMemoryService(db_session)
    assert await memory.get_current_relationships(repository_row.id) == []
