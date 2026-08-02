"""ADR 0018 RFC-04 — `EngineeringMemoryRepository`/`EngineeringMemoryService`
against a real Postgres transaction (`db_session` fixture, rolled back per
test — see `tests/conftest.py`). No mocks on the persistence path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.correction import CorrectionSource, UserCorrection
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _identity() -> GeneratorIdentity:
    return GeneratorIdentity(kind="deterministic", name="test_parser", version="1.0.0")


def _provenance(pack_id: str = "pack-1") -> Provenance:
    return Provenance(
        generator=_identity(),
        produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        pack_id=pack_id,
        pack_version="v1",
        run_id=pack_id,
    )


def _evidence_pack(repository_id: str, pack_id: str = "pack-1") -> EngineeringEvidencePack:
    item = EvidenceItem(
        id="evidence-1",
        kind="graph_node:Repository",
        source_type="code",
        reliability_tier=3,
        reference=EvidenceReference(
            repository_id=repository_id, source_type="code", locator="src/app.py", key="k"
        ),
        raw_value='{"language": "python"}',
        provenance=_provenance(pack_id),
    )
    return EngineeringEvidencePack(
        id=pack_id,
        repository_id=repository_id,
        commit_sha="abc123",
        schema_version="v1",
        items=(item,),
        produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
    )


def _relationship(
    *, state: ConfidenceState = ConfidenceState.HIGHLY_LIKELY, tier: int = 3
) -> KnowledgeRelationship:
    confidence = ConfidenceModel(
        state=state,
        distinct_confirming_source_types=1,
        confirming_source_types=frozenset({"code_annotation_literal"}),
        max_confirming_reliability_tier=tier,
        contradiction_count=0,
        computed_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        formula_version="v1",
    )
    return KnowledgeRelationship(
        id="rel-1",
        relationship_type="CONTAINS",
        source_entity="repo-1:repository",
        target_entity="repo-1:module:app",
        confidence=confidence,
        hypothesis_ids=("hyp-1",),
        provenance=(_provenance(),),
    )


@pytest.fixture
async def repository_id(db_session: AsyncSession) -> uuid.UUID:
    """A real `repositories` row (FK target) — `EngineeringEvidencePackRecord`/
    `KnowledgeRelationshipRecord` both FK to it."""
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


class TestEvidencePackPersistence:
    async def test_store_and_retrieve_round_trips_exactly(
        self, db_session: AsyncSession, repository_id: uuid.UUID
    ):
        service = EngineeringMemoryService(db_session)
        pack = _evidence_pack(str(repository_id))

        record = await service.store_evidence_pack(repository_id, pack)
        assert record.id is not None
        assert record.item_count == 1
        assert record.compression == "gzip"
        # Actually compressed, not just re-encoded — the whole point of
        # RFC-04's "compressed blob, not relational rows" requirement.
        assert len(record.compressed_blob) < record.uncompressed_size_bytes + 64

        retrieved = await service.retrieve_evidence_pack(pack.id)
        assert retrieved == pack

    async def test_append_only_two_packs_for_same_commit_both_persist(
        self, db_session: AsyncSession, repository_id: uuid.UUID
    ):
        """Re-indexing an unchanged commit must not silently overwrite the
        prior pack — both rows must exist."""
        service = EngineeringMemoryService(db_session)
        pack_1 = _evidence_pack(str(repository_id), pack_id="pack-run-1")
        pack_2 = _evidence_pack(str(repository_id), pack_id="pack-run-2")

        await service.store_evidence_pack(repository_id, pack_1)
        await service.store_evidence_pack(repository_id, pack_2)

        all_packs = await service._repository.list_evidence_packs(repository_id)
        assert len(all_packs) == 2

    async def test_retrieve_nonexistent_pack_returns_none(self, db_session: AsyncSession):
        service = EngineeringMemoryService(db_session)
        assert await service.retrieve_evidence_pack("does-not-exist") is None


class TestKnowledgeRelationshipPersistence:
    async def test_store_relationship_and_read_current(
        self, db_session: AsyncSession, repository_id: uuid.UUID
    ):
        service = EngineeringMemoryService(db_session)
        relationship = _relationship()

        stored = await service.store_relationship(repository_id, relationship)
        assert stored.confidence_state == "highly_likely"

        current = await service.get_current_relationships(repository_id)
        assert len(current) == 1
        assert current[0].relationship_type == "CONTAINS"

    async def test_confidence_history_never_overwrites_prior_versions(
        self, db_session: AsyncSession, repository_id: uuid.UUID
    ):
        """The core append-only requirement: re-storing the same
        relationship at a different confidence state must add a new row,
        never mutate the old one — full history preserved."""
        service = EngineeringMemoryService(db_session)

        first = await service.store_relationship(
            repository_id, _relationship(state=ConfidenceState.LIKELY, tier=1)
        )
        second = await service.store_relationship(
            repository_id, _relationship(state=ConfidenceState.VERIFIED, tier=3)
        )

        assert first.id != second.id  # two distinct rows, not one mutated

        history = await service.get_relationship_history(
            repository_id, "CONTAINS", "repo-1:repository", "repo-1:module:app"
        )
        assert len(history) == 2
        assert [h.confidence_state for h in history] == ["likely", "verified"]  # chronological

        # "Current" is the latest version, not the first.
        current = await service.get_current_relationships(repository_id)
        assert len(current) == 1
        assert current[0].confidence_state == "verified"
        assert current[0].id == second.id

    async def test_batch_store_commits_once_for_many_relationships(
        self, db_session: AsyncSession, repository_id: uuid.UUID
    ):
        service = EngineeringMemoryService(db_session)
        relationships = [
            KnowledgeRelationship(
                id=f"rel-{i}",
                relationship_type="CONTAINS",
                source_entity="repo-1:repository",
                target_entity=f"repo-1:module:m{i}",
                confidence=_relationship().confidence,
                hypothesis_ids=(f"hyp-{i}",),
                provenance=(_provenance(),),
            )
            for i in range(5)
        ]
        stored = await service.store_relationships(repository_id, relationships)
        assert len(stored) == 5
        current = await service.get_current_relationships(repository_id)
        assert len(current) == 5


class TestUserCorrectionPersistence:
    async def test_apply_and_retrieve_correction(
        self, db_session: AsyncSession, repository_id: uuid.UUID
    ):
        service = EngineeringMemoryService(db_session)
        await service.store_relationship(repository_id, _relationship())

        correction = UserCorrection(
            id="correction-1",
            relationship_id="rel-1",
            source=CorrectionSource(kind="human", identity="user-1", trust_level=1.0),
            corrected_state=ConfidenceState.REJECTED,
            reason="This module was removed last sprint.",
            created_at=datetime(2026, 8, 1, 13, 0, 0, tzinfo=UTC),
        )
        stored = await service.apply_correction(
            repository_id, "CONTAINS", "repo-1:repository", "repo-1:module:app", correction
        )
        assert stored.correction_source_kind == "human"
        assert stored.corrected_state == "rejected"

        corrections = await service.get_corrections(
            repository_id, "CONTAINS", "repo-1:repository", "repo-1:module:app"
        )
        assert len(corrections) == 1
        assert corrections[0].reason == "This module was removed last sprint."

    async def test_correction_never_mutates_the_relationship_history(
        self, db_session: AsyncSession, repository_id: uuid.UUID
    ):
        """ADR 0018 invariant: even a human correction is a new
        transition, never a silent edit to the relationship's own
        history rows."""
        service = EngineeringMemoryService(db_session)
        stored_relationship = await service.store_relationship(repository_id, _relationship())

        correction = UserCorrection(
            id="correction-1",
            relationship_id="rel-1",
            source=CorrectionSource(kind="human", identity="user-1", trust_level=1.0),
            corrected_state=None,  # outright rejection
            reason="False positive.",
            created_at=datetime(2026, 8, 1, 13, 0, 0, tzinfo=UTC),
        )
        await service.apply_correction(
            repository_id, "CONTAINS", "repo-1:repository", "repo-1:module:app", correction
        )

        history = await service.get_relationship_history(
            repository_id, "CONTAINS", "repo-1:repository", "repo-1:module:app"
        )
        assert len(history) == 1  # unchanged by the correction
        assert history[0].id == stored_relationship.id
        assert history[0].confidence_state == "highly_likely"  # untouched
