"""`ArchitectureInsightService.detect_findings` against real Postgres."""

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
from app.services.engineering_intelligence.architecture_insight_service import detect_findings

pytestmark = pytest.mark.asyncio


def _relationship(rel_type: str, source: str, target: str) -> KnowledgeRelationship:
    return KnowledgeRelationship(
        id=f"rel-{source}-{target}-{rel_type}",
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
async def two_repositories(db_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    user = User(email=f"test-{uuid.uuid4()}@example.com", full_name="Test User")
    db_session.add(user)
    await db_session.flush()

    repos = []
    for name in ("repo-a", "repo-b"):
        repo = Repository(
            user_id=user.id,
            owner="test-owner",
            name=name,
            full_name=f"test-owner/{name}",
            html_url=f"https://github.com/test-owner/{name}",
            default_branch="main",
            source="github",
            github_repo_id=str(uuid.uuid4().int)[:10],
        )
        db_session.add(repo)
        await db_session.flush()
        repos.append(repo.id)
    return repos[0], repos[1]


async def test_detect_findings_reports_dependency_cycle(
    db_session: AsyncSession, two_repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo_a, repo_b = two_repositories
    memory = EngineeringMemoryService(db_session)
    a_node = f"{repo_a}:repository"
    b_node = f"{repo_b}:repository"

    await memory.store_relationship(repo_a, _relationship("DEPENDS_ON_REPOSITORY", a_node, b_node))
    await memory.store_relationship(repo_b, _relationship("DEPENDS_ON_REPOSITORY", b_node, a_node))

    findings = await detect_findings(db_session, [repo_a, repo_b])

    cycle_findings = [f for f in findings if f.finding_type == "dependency_cycle"]
    assert len(cycle_findings) == 1
    assert set(cycle_findings[0].involved_repositories) == {a_node, b_node}


async def test_detect_findings_reports_shared_database(
    db_session: AsyncSession, two_repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo_a, repo_b = two_repositories
    memory = EngineeringMemoryService(db_session)

    await memory.store_relationship(
        repo_a, _relationship("WRITES_TO", f"{repo_a}:svc:x", "shared:table:orders")
    )
    await memory.store_relationship(
        repo_b, _relationship("READS_FROM", f"{repo_b}:svc:y", "shared:table:orders")
    )

    findings = await detect_findings(db_session, [repo_a, repo_b])

    shared_db_findings = [f for f in findings if f.finding_type == "shared_database"]
    assert len(shared_db_findings) == 1
    assert set(shared_db_findings[0].involved_repositories) == {str(repo_a), str(repo_b)}


async def test_detect_findings_returns_empty_for_no_cross_repo_activity(
    db_session: AsyncSession, two_repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo_a, _repo_b = two_repositories
    findings = await detect_findings(db_session, [repo_a])
    assert findings == ()
