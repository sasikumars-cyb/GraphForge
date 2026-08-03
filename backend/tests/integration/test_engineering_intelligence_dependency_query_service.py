"""`DependencyQueryService.search` against real Postgres."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User
from app.services.engineering_intelligence.dependency_query_service import search

pytestmark = pytest.mark.asyncio


def _relationship(rel_type: str, source: str, target: str) -> KnowledgeRelationship:
    return KnowledgeRelationship(
        id=f"rel-{source}-{target}",
        relationship_type=rel_type,
        source_entity=source,
        target_entity=target,
        confidence=ConfidenceModel(
            state=ConfidenceState.LIKELY,
            distinct_confirming_source_types=1,
            confirming_source_types=frozenset({"code_annotation_literal"}),
            max_confirming_reliability_tier=3,
            contradiction_count=0,
            computed_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
            formula_version="v1",
        ),
        hypothesis_ids=("hyp-1",),
        provenance=(
            Provenance(
                generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
                produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
                pack_id="pack-1",
                pack_version="v1",
                run_id="run-1",
            ),
        ),
    )


@pytest.fixture
async def repository_id(db_session: AsyncSession) -> uuid.UUID:
    user = User(email=f"test-{uuid.uuid4()}@example.com", full_name="Test User")
    db_session.add(user)
    await db_session.flush()
    repo = Repository(
        user_id=user.id,
        owner="test-owner",
        name="test-repo",
        full_name="test-owner/test-repo",
        html_url="https://github.com/test-owner/test-repo",
        default_branch="main",
        source="github",
        github_repo_id=str(uuid.uuid4().int)[:10],
    )
    db_session.add(repo)
    await db_session.flush()
    return repo.id


async def test_search_filters_by_relationship_type_and_keyword(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    memory = EngineeringMemoryService(db_session)
    await memory.store_relationship(
        repository_id, _relationship("CALLS_SERVICE", "repo-1:svc:checkout", "repo-1:svc:billing")
    )
    await memory.store_relationship(
        repository_id, _relationship("PRODUCES_TO", "repo-1:svc:checkout", "repo-1:topic:orders")
    )

    result = await search(db_session, [repository_id], relationship_type="CALLS_SERVICE")
    assert result.total_matched == 1
    assert result.relationships[0].relationship_type == "CALLS_SERVICE"

    result = await search(db_session, [repository_id], keyword="billing")
    assert result.total_matched == 1
    assert result.relationships[0].target_entity == "repo-1:svc:billing"

    result = await search(db_session, [repository_id])
    assert result.total_matched == 2


async def test_search_returns_empty_for_no_repositories_in_scope(
    db_session: AsyncSession,
) -> None:
    result = await search(db_session, [])
    assert result == result.__class__()
