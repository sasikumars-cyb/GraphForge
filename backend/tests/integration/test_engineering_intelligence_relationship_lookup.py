"""`relationship_lookup.fetch_with_confidence` against a real Postgres
transaction (`db_session` fixture, rolled back per test) — same
no-mocks-on-persistence convention as `tests/integration/test_engineering_memory.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.explanation import ConfidenceExplanation
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User
from app.services.engineering_intelligence.relationship_lookup import fetch_with_confidence

pytestmark = pytest.mark.asyncio


def _relationship(source: str, target: str, state: ConfidenceState) -> KnowledgeRelationship:
    confidence = ConfidenceModel(
        state=state,
        distinct_confirming_source_types=1,
        confirming_source_types=frozenset({"code_annotation_literal"}),
        max_confirming_reliability_tier=3,
        contradiction_count=0,
        computed_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        formula_version="v1",
    )
    return KnowledgeRelationship(
        id=f"rel-{source}-{target}",
        relationship_type="CALLS_SERVICE",
        source_entity=source,
        target_entity=target,
        confidence=confidence,
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


def _explanation() -> ConfidenceExplanation:
    return ConfidenceExplanation(
        state=ConfidenceState.HIGHLY_LIKELY,
        confirming_domains=("code_annotation_literal",),
        strongest_domain="code_annotation_literal",
        contradicting_domains=(),
        why_confidence_increased="Confirmed by static analysis.",
        why_confidence_limited="Only one confirming domain.",
        recommendations=(),
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


async def test_fetch_with_confidence_returns_current_relationships_sorted(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    memory = EngineeringMemoryService(db_session)
    await memory.store_relationship(
        repository_id,
        _relationship("repo-1:svc:b", "repo-1:svc:a", ConfidenceState.LIKELY),
    )
    await memory.store_relationship(
        repository_id,
        _relationship("repo-1:svc:a", "repo-1:svc:c", ConfidenceState.HIGHLY_LIKELY),
        _explanation(),
    )

    insights = await fetch_with_confidence(db_session, repository_id)

    assert len(insights) == 2
    assert [i.relationship_key for i in insights] == sorted(i.relationship_key for i in insights)
    with_explanation = next(i for i in insights if i.source_entity == "repo-1:svc:a")
    assert with_explanation.confidence_state == "highly_likely"
    assert with_explanation.explanation is not None
    assert with_explanation.explanation.strongest_domain == "code_annotation_literal"

    without_explanation = next(i for i in insights if i.source_entity == "repo-1:svc:b")
    assert without_explanation.explanation is None


async def test_fetch_with_confidence_returns_empty_for_unindexed_repository(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    insights = await fetch_with_confidence(db_session, repository_id)
    assert insights == ()
