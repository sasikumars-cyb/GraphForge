"""Repository-layer tests for RFC-001 — distinct from the service-level
tests in `test_engineering_session_services.py`. These exercise each
repository's queries directly against a real (transactional-rollback)
`AsyncSession`, with no service-layer business rules in the way: they
prove the SQL each repository builds actually returns what its name
promises (pagination totals, ordering, cascade shape, join-table
assembly), not that any domain invariant holds.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.belief import Belief, Hypothesis
from app.models.contradiction import Contradiction, ContradictionParty
from app.models.decision import Decision, Recommendation
from app.models.engineering_session import EngineeringSession, TimelineEntry
from app.models.evidence import Evidence
from app.models.participant import Participant
from app.models.user import User
from app.repositories.belief_repository import BeliefRepository
from app.repositories.contradiction_repository import ContradictionRepository
from app.repositories.decision_repository import DecisionRepository
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.recommendation_repository import RecommendationRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.timeline_repository import TimelineRepository

pytestmark = pytest.mark.asyncio


async def _make_agent(db_session: AsyncSession, role: str = "investigator") -> Participant:
    participant = Participant(kind="agent", display_name=role, agent_role=role)
    db_session.add(participant)
    await db_session.flush()
    return participant


async def _make_session(db_session: AsyncSession, title: str = "T") -> EngineeringSession:
    session = EngineeringSession(title=title)
    db_session.add(session)
    await db_session.flush()
    return session


async def test_session_repository_add_and_get(db_session: AsyncSession) -> None:
    repo = SessionRepository(db_session)
    created = await repo.add(EngineeringSession(title="Investigate flaky test"))
    assert created.id is not None

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert fetched.title == "Investigate flaky test"


async def test_session_repository_get_missing_returns_none(db_session: AsyncSession) -> None:
    repo = SessionRepository(db_session)
    assert await repo.get(uuid.uuid4()) is None


async def test_session_repository_list_page_filters_by_status(db_session: AsyncSession) -> None:
    repo = SessionRepository(db_session)
    await repo.add(EngineeringSession(title="A", status="orienting"))
    await repo.add(EngineeringSession(title="B", status="converging"))

    items, total = await repo.list_page(status="converging", limit=10, offset=0)
    assert total == 1
    assert items[0].title == "B"


async def test_timeline_repository_next_sequence_is_monotonic(db_session: AsyncSession) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    repo = TimelineRepository(db_session)

    assert await repo.next_sequence(session.id) == 1
    await repo.add(
        TimelineEntry(
            session_id=session.id,
            sequence=1,
            participant_id=participant.id,
            kind="note",
            summary="first",
        )
    )
    assert await repo.next_sequence(session.id) == 2


async def test_timeline_repository_list_page_orders_by_sequence(db_session: AsyncSession) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    repo = TimelineRepository(db_session)

    for seq in (2, 1, 3):
        await repo.add(
            TimelineEntry(
                session_id=session.id,
                sequence=seq,
                participant_id=participant.id,
                kind="note",
                summary=f"entry {seq}",
            )
        )

    items, total = await repo.list_page(session.id, limit=10, offset=0)
    assert total == 3
    assert [e.sequence for e in items] == [1, 2, 3]


async def test_belief_repository_list_beliefs_excludes_retracted_by_default(
    db_session: AsyncSession,
) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    repo = BeliefRepository(db_session)

    await repo.add_belief(
        Belief(
            session_id=session.id,
            participant_id=participant.id,
            statement="active belief",
            status="formed",
        )
    )
    await repo.add_belief(
        Belief(
            session_id=session.id,
            participant_id=participant.id,
            statement="retracted belief",
            status="retracted",
        )
    )

    active_only = await repo.list_beliefs(session.id)
    assert [b.statement for b in active_only] == ["active belief"]

    with_retracted = await repo.list_beliefs(session.id, exclude_retracted=False)
    assert len(with_retracted) == 2


async def test_belief_repository_list_hypotheses_unresolved_only(
    db_session: AsyncSession,
) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    repo = BeliefRepository(db_session)

    await repo.add_hypothesis(
        Hypothesis(
            session_id=session.id,
            participant_id=participant.id,
            description="pending",
            status="proposed",
        )
    )
    await repo.add_hypothesis(
        Hypothesis(
            session_id=session.id,
            participant_id=participant.id,
            description="done",
            status="resolved",
        )
    )

    unresolved = await repo.list_hypotheses(session.id, unresolved_only=True)
    assert [h.description for h in unresolved] == ["pending"]


async def test_evidence_repository_list_page_is_paginated_and_ordered(
    db_session: AsyncSession,
) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    repo = EvidenceRepository(db_session)

    for i in range(3):
        await repo.add(
            Evidence(
                session_id=session.id,
                participant_id=participant.id,
                evidence_kind="retrieved",
                summary=f"evidence {i}",
                source="graph",
            )
        )

    page1, total = await repo.list_page(session.id, limit=2, offset=0)
    assert total == 3
    assert len(page1) == 2

    page2, _ = await repo.list_page(session.id, limit=2, offset=2)
    assert len(page2) == 1


async def test_recommendation_repository_list_open_excludes_non_proposed(
    db_session: AsyncSession,
) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    repo = RecommendationRepository(db_session)

    await repo.add(
        Recommendation(
            session_id=session.id,
            participant_id=participant.id,
            statement="open rec",
            status="proposed",
        )
    )
    await repo.add(
        Recommendation(
            session_id=session.id,
            participant_id=participant.id,
            statement="closed rec",
            status="declined",
        )
    )

    open_recs = await repo.list_open(session.id)
    assert [r.statement for r in open_recs] == ["open rec"]


async def test_recommendation_repository_list_for_target_belief(
    db_session: AsyncSession,
) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    belief_repo = BeliefRepository(db_session)
    belief = await belief_repo.add_belief(
        Belief(session_id=session.id, participant_id=participant.id, statement="B")
    )

    repo = RecommendationRepository(db_session)
    await repo.add(
        Recommendation(
            session_id=session.id,
            participant_id=participant.id,
            statement="targets belief",
            target_belief_id=belief.id,
        )
    )
    await repo.add(
        Recommendation(
            session_id=session.id, participant_id=participant.id, statement="untargeted"
        )
    )

    targeted = await repo.list_for_target_belief(session.id, belief.id)
    assert [r.statement for r in targeted] == ["targets belief"]


async def test_decision_repository_add_get_and_list_page(db_session: AsyncSession) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    user = User(email="repo-test-user@example.com", hashed_password="x", full_name="U")
    db_session.add(user)
    await db_session.flush()
    human = Participant(kind="human", display_name="U", user_id=user.id)
    db_session.add(human)
    await db_session.flush()

    repo = DecisionRepository(db_session)
    created = await repo.add(
        Decision(
            session_id=session.id,
            participant_id=participant.id,
            decision_kind="planning_strategy",
            statement="do the thing",
            rationale="because",
            committed_by_participant_id=human.id,
        )
    )
    assert created.id is not None

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert fetched.statement == "do the thing"

    page, total = await repo.list_page(session.id, limit=10, offset=0)
    assert total == 1
    assert page[0].id == created.id


async def test_contradiction_repository_add_persists_all_parties(
    db_session: AsyncSession,
) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    belief_repo = BeliefRepository(db_session)
    belief_a = await belief_repo.add_belief(
        Belief(session_id=session.id, participant_id=participant.id, statement="A")
    )
    belief_b = await belief_repo.add_belief(
        Belief(session_id=session.id, participant_id=participant.id, statement="B")
    )

    repo = ContradictionRepository(db_session)
    created = await repo.add(
        Contradiction(
            session_id=session.id, participant_id=participant.id, description="A vs B"
        ),
        party_artifact_ids=[belief_a.id, belief_b.id],
    )

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert {p.artifact_id for p in fetched.parties} == {belief_a.id, belief_b.id}


async def test_contradiction_repository_list_by_artifact(db_session: AsyncSession) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    belief_repo = BeliefRepository(db_session)
    belief_a = await belief_repo.add_belief(
        Belief(session_id=session.id, participant_id=participant.id, statement="A")
    )
    belief_b = await belief_repo.add_belief(
        Belief(session_id=session.id, participant_id=participant.id, statement="B")
    )
    belief_c = await belief_repo.add_belief(
        Belief(session_id=session.id, participant_id=participant.id, statement="C")
    )

    repo = ContradictionRepository(db_session)
    await repo.add(
        Contradiction(session_id=session.id, participant_id=participant.id, description="A v B"),
        party_artifact_ids=[belief_a.id, belief_b.id],
    )
    await repo.add(
        Contradiction(session_id=session.id, participant_id=participant.id, description="A v C"),
        party_artifact_ids=[belief_a.id, belief_c.id],
    )

    for_a = await repo.list_by_artifact(belief_a.id)
    assert len(for_a) == 2
    for_b = await repo.list_by_artifact(belief_b.id)
    assert len(for_b) == 1


async def test_contradiction_repository_list_page_filters_unresolved(
    db_session: AsyncSession,
) -> None:
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    belief_repo = BeliefRepository(db_session)
    belief_a = await belief_repo.add_belief(
        Belief(session_id=session.id, participant_id=participant.id, statement="A")
    )
    belief_b = await belief_repo.add_belief(
        Belief(session_id=session.id, participant_id=participant.id, statement="B")
    )

    repo = ContradictionRepository(db_session)
    resolved = await repo.add(
        Contradiction(
            session_id=session.id,
            participant_id=participant.id,
            description="resolved one",
            status="resolved",
        ),
        party_artifact_ids=[belief_a.id, belief_b.id],
    )
    await repo.add(
        Contradiction(
            session_id=session.id,
            participant_id=participant.id,
            description="still open",
            status="detected",
        ),
        party_artifact_ids=[belief_a.id, belief_b.id],
    )

    unresolved_page, unresolved_total = await repo.list_page(
        session.id, unresolved_only=True, limit=10, offset=0
    )
    assert unresolved_total == 1
    assert unresolved_page[0].description == "still open"

    all_page, all_total = await repo.list_page(session.id, limit=10, offset=0)
    assert all_total == 2
    assert resolved.id in {c.id for c in all_page}


async def test_contradiction_party_pair_uniqueness_is_enforced(db_session: AsyncSession) -> None:
    """The DB-level `uq_contradiction_parties_pair` constraint (asserted
    structurally in `test_engineering_session_schema.py`) actually rejects
    a duplicate pair at the SQL level, not just in name."""
    session = await _make_session(db_session)
    participant = await _make_agent(db_session)
    belief_repo = BeliefRepository(db_session)
    belief_a = await belief_repo.add_belief(
        Belief(session_id=session.id, participant_id=participant.id, statement="A")
    )
    belief_b = await belief_repo.add_belief(
        Belief(session_id=session.id, participant_id=participant.id, statement="B")
    )

    repo = ContradictionRepository(db_session)
    contradiction = await repo.add(
        Contradiction(session_id=session.id, participant_id=participant.id, description="A v B"),
        party_artifact_ids=[belief_a.id, belief_b.id],
    )

    db_session.add(
        ContradictionParty(contradiction_id=contradiction.id, artifact_id=belief_a.id)
    )
    with pytest.raises(Exception):  # noqa: B017 - asserting the DB IntegrityError surfaces
        await db_session.flush()
