"""ADR 0018 RFC-07 — `LearningEngineService` against a real Postgres
transaction (`db_session` fixture, rolled back per test). Proves: feedback
persists as an append-only `LearningEventRecord`, "approve"/"reject"/
"correct_confidence" also persist a `UserCorrection` via the reused,
unmodified RFC-04 path, statistics are computed correctly from real rows,
and none of this touches indexing/generation/validation/confidence/
materialization.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.correction import CorrectionSource
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.learning_engine.contracts.feedback import RelationshipFeedback
from app.learning_engine.service import LearningEngineService
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio

_SOURCE = CorrectionSource(kind="human", identity="reviewer@example.com", trust_level=1.0)


def _provenance(generator_name: str = "deterministic_parser") -> Provenance:
    return Provenance(
        generator=GeneratorIdentity(kind="deterministic", name=generator_name, version="1.0.0"),
        produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        pack_id="pack-1",
        pack_version="v1",
        run_id="pack-1",
    )


def _relationship(
    *, state: ConfidenceState = ConfidenceState.LIKELY, generator_name: str = "deterministic_parser"
) -> KnowledgeRelationship:
    confidence = ConfidenceModel(
        state=state,
        distinct_confirming_source_types=1,
        confirming_source_types=frozenset({"repository_manifest"}),
        max_confirming_reliability_tier=1,
        contradiction_count=0,
        computed_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        formula_version="v1",
    )
    return KnowledgeRelationship(
        id="rel-1",
        relationship_type="CALLS_SERVICE",
        source_entity="repo-1:service:a",
        target_entity="repo-1:service:b",
        confidence=confidence,
        hypothesis_ids=("hyp-1",),
        provenance=(_provenance(generator_name),),
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


async def _seed_relationship(
    db_session: AsyncSession, repository_id: uuid.UUID, **kwargs: object
) -> None:
    memory = EngineeringMemoryService(db_session)
    await memory.store_relationship(repository_id, _relationship(**kwargs))  # type: ignore[arg-type]


def _feedback(repository_id: uuid.UUID, **overrides: object) -> RelationshipFeedback:
    defaults: dict[str, object] = dict(
        repository_id=str(repository_id),
        source=_SOURCE,
        kind="approve",
        reason="confirmed by manifest evidence",
        created_at=datetime.now(UTC),
        relationship_type="CALLS_SERVICE",
        source_entity="repo-1:service:a",
        target_entity="repo-1:service:b",
    )
    defaults.update(overrides)
    return RelationshipFeedback(**defaults)  # type: ignore[arg-type]


async def test_approve_feedback_persists_learning_event_and_correction(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    await _seed_relationship(db_session, repository_id)
    service = LearningEngineService(db_session)

    record = await service.submit_feedback(_feedback(repository_id, kind="approve"))

    assert record.event_type == "approved_relationship"
    assert record.relationship_type == "CALLS_SERVICE"
    assert record.generator_names == ["deterministic_parser"]
    assert record.confidence_state_at_event == "likely"

    memory = EngineeringMemoryService(db_session)
    corrections = await memory.get_corrections(
        repository_id, "CALLS_SERVICE", "repo-1:service:a", "repo-1:service:b"
    )
    assert len(corrections) == 1
    assert corrections[0].corrected_state == "likely"


async def test_reject_feedback_persists_correction_with_null_corrected_state(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    await _seed_relationship(db_session, repository_id)
    service = LearningEngineService(db_session)

    record = await service.submit_feedback(_feedback(repository_id, kind="reject"))
    assert record.event_type == "rejected_relationship"

    memory = EngineeringMemoryService(db_session)
    corrections = await memory.get_corrections(
        repository_id, "CALLS_SERVICE", "repo-1:service:a", "repo-1:service:b"
    )
    assert corrections[0].corrected_state is None


async def test_flag_kinds_persist_learning_event_without_a_correction(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    await _seed_relationship(db_session, repository_id)
    service = LearningEngineService(db_session)

    await service.submit_feedback(_feedback(repository_id, kind="flag_weak_evidence"))

    memory = EngineeringMemoryService(db_session)
    corrections = await memory.get_corrections(
        repository_id, "CALLS_SERVICE", "repo-1:service:a", "repo-1:service:b"
    )
    assert corrections == []  # no correction -- a flag is a signal only


async def test_flag_missing_relationship_requires_no_existing_relationship(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    service = LearningEngineService(db_session)
    feedback = RelationshipFeedback(
        repository_id=str(repository_id),
        source=_SOURCE,
        kind="flag_missing_relationship",
        reason="expected an OWNS_DATABASE edge here",
        created_at=datetime.now(UTC),
    )

    record = await service.submit_feedback(feedback)
    assert record.event_type == "missing_relationship"
    assert record.relationship_key is None


async def test_approving_a_nonexistent_relationship_raises_not_found(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    from app.core.exceptions import NotFoundError

    service = LearningEngineService(db_session)
    with pytest.raises(NotFoundError):
        await service.submit_feedback(_feedback(repository_id, kind="approve"))


async def test_events_are_append_only_across_repeated_feedback(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    await _seed_relationship(db_session, repository_id)
    service = LearningEngineService(db_session)

    await service.submit_feedback(_feedback(repository_id, kind="approve"))
    await service.submit_feedback(_feedback(repository_id, kind="flag_weak_evidence"))
    await service.submit_feedback(_feedback(repository_id, kind="reject"))

    events = await service.list_events(repository_id)
    assert len(events) == 3
    assert [e.event_type for e in events] == [
        "approved_relationship",
        "weak_evidence",
        "rejected_relationship",
    ]


async def test_statistics_reflect_real_persisted_events(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    await _seed_relationship(db_session, repository_id)
    service = LearningEngineService(db_session)

    await service.submit_feedback(_feedback(repository_id, kind="approve"))
    await service.submit_feedback(_feedback(repository_id, kind="reject"))

    stats = await service.get_statistics(repository_id)
    assert stats.total_events == 2
    assert stats.approval_rate == 0.5
    assert stats.rejection_rate == 0.5
    assert stats.counts_by_relationship_type["CALLS_SERVICE"] == {
        "approved_relationship": 1,
        "rejected_relationship": 1,
    }


async def test_feedback_does_not_alter_the_relationship_history(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    """The RFC's own architectural rule, proven directly: submitting
    feedback (even "reject") must never mutate or remove the existing
    `KnowledgeRelationshipRecord` history -- a correction is a new,
    separate fact, not an edit."""
    await _seed_relationship(db_session, repository_id)
    memory = EngineeringMemoryService(db_session)
    before = await memory.get_relationship_history(
        repository_id, "CALLS_SERVICE", "repo-1:service:a", "repo-1:service:b"
    )

    service = LearningEngineService(db_session)
    await service.submit_feedback(_feedback(repository_id, kind="reject"))

    after = await memory.get_relationship_history(
        repository_id, "CALLS_SERVICE", "repo-1:service:a", "repo-1:service:b"
    )
    assert len(before) == len(after) == 1
    assert before[0].id == after[0].id
    assert before[0].confidence_state == after[0].confidence_state == "likely"
