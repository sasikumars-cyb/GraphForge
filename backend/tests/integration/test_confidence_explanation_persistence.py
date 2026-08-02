"""ADR 0018 — Confidence Explainability persisted alongside
`KnowledgeRelationship`, real Postgres round-trip."""

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

pytestmark = pytest.mark.asyncio


def _provenance() -> Provenance:
    return Provenance(
        generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
        produced_at=datetime.now(UTC),
        pack_id="pack:1",
        pack_version="v1",
        run_id="pack:1",
    )


def _relationship() -> KnowledgeRelationship:
    return KnowledgeRelationship(
        id="rel:repo-1:repository:OWNS_DATABASE:repo-1:capability:database",
        relationship_type="OWNS_DATABASE",
        source_entity="repo-1:repository",
        target_entity="repo-1:capability:database",
        confidence=ConfidenceModel(
            state=ConfidenceState.LIKELY,
            distinct_confirming_source_types=1,
            confirming_source_types=frozenset({"repository_manifest"}),
            max_confirming_reliability_tier=1,
            contradiction_count=0,
            computed_at=datetime.now(UTC),
            formula_version="v1",
        ),
        hypothesis_ids=("hyp:1",),
        provenance=(_provenance(),),
    )


def _explanation() -> ConfidenceExplanation:
    return ConfidenceExplanation(
        state=ConfidenceState.LIKELY,
        confirming_domains=("repository_manifest",),
        strongest_domain="repository_manifest",
        contradicting_domains=(),
        why_confidence_increased="Confirmed by 1 independent evidence domain(s).",
        why_confidence_limited="No confirming domain reached the high-reliability tier.",
        recommendations=("Documentation evidence was checked but did not confirm.",),
    )


@pytest.fixture
async def repository_id(db_session: AsyncSession) -> uuid.UUID:
    user = User(email=f"test-{uuid.uuid4()}@example.com", full_name="Test User")
    db_session.add(user)
    await db_session.flush()
    repo = Repository(
        user_id=user.id,
        owner="test-owner",
        name="repo",
        full_name="test-owner/repo",
        html_url="https://github.com/test-owner/repo",
        default_branch="main",
        source="github",
        github_repo_id=str(uuid.uuid4().int)[:10],
    )
    db_session.add(repo)
    await db_session.flush()
    return repo.id


async def test_store_relationship_persists_explanation(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    service = EngineeringMemoryService(db_session)

    stored = await service.store_relationship(repository_id, _relationship(), _explanation())

    assert stored.explanation is not None
    assert stored.explanation["confirming_domains"] == ["repository_manifest"]
    assert stored.explanation["strongest_domain"] == "repository_manifest"

    current = await service.get_current_relationships(repository_id)
    assert len(current) == 1
    assert current[0].explanation == stored.explanation


async def test_store_relationship_without_explanation_is_backward_compatible(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    service = EngineeringMemoryService(db_session)

    stored = await service.store_relationship(repository_id, _relationship())

    assert stored.explanation is None


async def test_store_relationships_batch_aligns_explanations_positionally(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    service = EngineeringMemoryService(db_session)
    rel_a = _relationship()
    rel_b = KnowledgeRelationship(
        id="rel:repo-1:repository:CONTAINS_CACHING:repo-1:capability:caching",
        relationship_type="CONTAINS_CACHING",
        source_entity="repo-1:repository",
        target_entity="repo-1:capability:caching",
        confidence=rel_a.confidence,
        hypothesis_ids=("hyp:2",),
        provenance=(_provenance(),),
    )

    stored = await service.store_relationships(
        repository_id, [rel_a, rel_b], [_explanation(), None]
    )

    assert stored[0].explanation is not None
    assert stored[1].explanation is None


async def test_store_relationships_rejects_mismatched_explanation_length(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    service = EngineeringMemoryService(db_session)

    with pytest.raises(ValueError, match="same length"):
        await service.store_relationships(repository_id, [_relationship()], [])
