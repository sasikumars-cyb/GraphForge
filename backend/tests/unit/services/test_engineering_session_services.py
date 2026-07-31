"""RFC-001 service-level tests — Architecture v2.1 §2.2's Engineering
Session aggregate. Exercises every domain invariant called out in the
architecture: aggregate ownership/composition, the propose/commit
boundary, N-ary Contradiction, competing-Recommendation conflict
detection, and append-only Evidence/Timeline.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models.user import User
from app.services.belief_service import BeliefService
from app.services.contradiction_service import ContradictionService
from app.services.decision_service import DecisionService
from app.services.evidence_service import EvidenceService
from app.services.participant_helpers import (
    get_or_create_agent_participant,
    get_or_create_human_participant,
)
from app.services.recommendation_service import RecommendationService
from app.services.session_service import SessionService
from app.services.timeline_service import TimelineService
from app.services.understanding_service import UnderstandingService

pytestmark = pytest.mark.asyncio


async def _make_user(db: AsyncSession, email: str = "engineer@example.com") -> User:
    user = User(email=email, full_name="Engineer", hashed_password="x")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# --- SessionService ----------------------------------------------------------


async def test_create_session_starts_orienting(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    session = await SessionService(db_session).create_session(
        title="Investigate duplicate records", created_by=user
    )
    assert session.status == "orienting"
    assert session.title == "Investigate duplicate records"


async def test_get_session_not_found_raises(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await SessionService(db_session).get_session(uuid.uuid4())


async def test_transition_status_accepts_any_valid_v21_state(db_session: AsyncSession) -> None:
    """Architecture v2.1 §3.1: "not a pipeline... every later state can
    reopen an earlier one" — a jump straight to "converging" then back to
    "investigating" must both succeed."""
    user = await _make_user(db_session)
    session_service = SessionService(db_session)
    session = await session_service.create_session(title="T", created_by=user)
    participant = await get_or_create_human_participant(db_session, user)

    updated = await session_service.transition_status(
        session.id, new_status="converging", participant_id=participant.id
    )
    assert updated.status == "converging"

    reopened = await session_service.transition_status(
        session.id, new_status="investigating", participant_id=participant.id
    )
    assert reopened.status == "investigating"


async def test_transition_status_rejects_an_unknown_status(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    session_service = SessionService(db_session)
    session = await session_service.create_session(title="T", created_by=user)
    participant = await get_or_create_human_participant(db_session, user)

    with pytest.raises(ConflictError):
        await session_service.transition_status(
            session.id, new_status="not-a-real-status", participant_id=participant.id
        )


async def test_list_sessions_paginates(db_session: AsyncSession) -> None:
    # Baseline first — `list_sessions` is intentionally unscoped (no
    # per-Organization/Mission filter exists yet, see RFC-001.md's Known
    # Limitations), so this asserts the *increase* this test caused rather
    # than an absolute count, which would be fragile against whatever else
    # already exists in a shared database.
    user = await _make_user(db_session)
    session_service = SessionService(db_session)
    _, baseline_total = await session_service.list_sessions(limit=1, offset=0)

    created_ids = set()
    for i in range(3):
        session = await session_service.create_session(title=f"Session {i}", created_by=user)
        created_ids.add(session.id)

    page1, total = await session_service.list_sessions(limit=2, offset=0)
    assert total == baseline_total + 3
    assert len(page1) == 2

    all_items, _ = await session_service.list_sessions(limit=total, offset=0)
    assert created_ids.issubset({s.id for s in all_items})


# --- BeliefService / Hypothesis lifecycle -------------------------------


async def _session_and_investigator(db_session: AsyncSession):
    user = await _make_user(db_session)
    session = await SessionService(db_session).create_session(title="T", created_by=user)
    investigator = await get_or_create_agent_participant(db_session, "investigator")
    return session, investigator, user


async def test_resolve_hypothesis_creates_a_belief_and_links_provenance(
    db_session: AsyncSession,
) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)

    hypothesis = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="Merge logic owns the bug"
    )
    assert hypothesis.status == "proposed"

    belief = await belief_service.resolve_hypothesis(
        hypothesis.id,
        participant_id=investigator.id,
        belief_statement="SCDType2Merger owns the bug",
        belief_confidence=0.8,
    )
    assert belief.statement == "SCDType2Merger owns the bug"

    resolved_hypothesis = await belief_service.get_hypothesis(hypothesis.id)
    assert resolved_hypothesis.status == "resolved"
    assert resolved_hypothesis.resolved_belief_id == belief.id


async def test_reject_hypothesis_keeps_it_as_a_recorded_dead_end(
    db_session: AsyncSession,
) -> None:
    """Architecture v2.1 §3.2: "Rejected --> [*]: kept as a recorded dead
    end" — never deleted."""
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    hypothesis = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="Ingestion re-publishes"
    )

    rejected = await belief_service.reject_hypothesis(
        hypothesis.id, participant_id=investigator.id, reason="Ruled out — same offset key"
    )
    assert rejected.status == "rejected"

    still_there = await belief_service.get_hypothesis(hypothesis.id)
    assert still_there.status == "rejected"
    assert still_there.description == "Ingestion re-publishes"


async def test_cannot_resolve_an_already_resolved_hypothesis(db_session: AsyncSession) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    hypothesis = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="X"
    )
    await belief_service.resolve_hypothesis(
        hypothesis.id, participant_id=investigator.id, belief_statement="Y", belief_confidence=0.5
    )

    with pytest.raises(ConflictError):
        await belief_service.resolve_hypothesis(
            hypothesis.id,
            participant_id=investigator.id,
            belief_statement="Z",
            belief_confidence=0.5,
        )


async def test_confidence_out_of_range_is_rejected(db_session: AsyncSession) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    with pytest.raises(ConflictError):
        await BeliefService(db_session).propose_hypothesis(
            session.id, participant_id=investigator.id, description="X", confidence=1.5
        )


async def test_revise_belief_mutates_in_place(db_session: AsyncSession) -> None:
    """Architecture v2.1 §3.3: Working Understanding (and its Beliefs) are
    "mutated in place, never versioned" — same row, same id, updated
    fields."""
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    hypothesis = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="X"
    )
    belief = await belief_service.resolve_hypothesis(
        hypothesis.id, participant_id=investigator.id, belief_statement="Y", belief_confidence=0.5
    )

    revised = await belief_service.revise_belief(
        belief.id, participant_id=investigator.id, statement="Y, refined", confidence=0.9
    )
    assert revised.id == belief.id
    assert revised.statement == "Y, refined"
    assert revised.status == "revised"


async def test_retract_belief_never_deletes_the_row(db_session: AsyncSession) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    hypothesis = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="X"
    )
    belief = await belief_service.resolve_hypothesis(
        hypothesis.id, participant_id=investigator.id, belief_statement="Y", belief_confidence=0.5
    )

    await belief_service.retract_belief(
        belief.id, participant_id=investigator.id, reason="Contradicted by later evidence"
    )
    still_there = await belief_service.get_belief(belief.id)
    assert still_there.status == "retracted"


async def test_belief_service_exposes_no_promotion_method(db_session: AsyncSession) -> None:
    """Architecture v2.1 §2.2 (Δ v2.1): "a Belief is never itself
    promoted." The absence of any promote-shaped method is the actual
    enforcement — not a runtime check, a missing capability."""
    belief_service = BeliefService(db_session)
    assert not hasattr(belief_service, "promote_belief")
    assert not any("promote" in name for name in dir(belief_service))


# --- UnderstandingService ------------------------------------------------


async def test_working_understanding_excludes_retracted_beliefs_by_default(
    db_session: AsyncSession,
) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)

    h1 = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="A"
    )
    b1 = await belief_service.resolve_hypothesis(
        h1.id, participant_id=investigator.id, belief_statement="A confirmed", belief_confidence=0.9
    )
    h2 = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="B"
    )
    await belief_service.resolve_hypothesis(
        h2.id, participant_id=investigator.id, belief_statement="B confirmed", belief_confidence=0.5
    )
    await belief_service.retract_belief(b1.id, participant_id=investigator.id, reason="wrong")

    wu = await UnderstandingService(db_session).get_working_understanding(session.id)
    assert wu.belief_count == 1
    assert wu.beliefs[0].statement == "B confirmed"
    assert wu.overall_confidence == 0.5


async def test_working_understanding_is_empty_for_a_fresh_session(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    session = await SessionService(db_session).create_session(title="T", created_by=user)
    wu = await UnderstandingService(db_session).get_working_understanding(session.id)
    assert wu.belief_count == 0
    assert wu.overall_confidence == 0.0


# --- EvidenceService -----------------------------------------------------


async def test_evidence_is_recorded_and_retrievable(db_session: AsyncSession) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    evidence = await EvidenceService(db_session).record(
        session.id,
        participant_id=investigator.id,
        evidence_kind="retrieved",
        summary="Traced the merge path",
        source="graph",
        payload={"hop_distance": 0},
    )
    assert evidence.evidence_kind == "retrieved"
    assert evidence.payload == {"hop_distance": 0}

    fetched = await EvidenceService(db_session).get(evidence.id)
    assert fetched.id == evidence.id


async def test_evidence_kind_is_validated(db_session: AsyncSession) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    with pytest.raises(ConflictError):
        await EvidenceService(db_session).record(
            session.id,
            participant_id=investigator.id,
            evidence_kind="not-a-real-kind",
            summary="x",
            source="graph",
        )


async def test_evidence_service_exposes_no_update_or_delete(db_session: AsyncSession) -> None:
    evidence_service = EvidenceService(db_session)
    assert not hasattr(evidence_service, "update")
    assert not hasattr(evidence_service, "delete")


# --- RecommendationService — finding 5's resolution ----------------------


async def test_competing_recommendations_for_the_same_belief_are_auto_contradicted(
    db_session: AsyncSession,
) -> None:
    """Architecture v2.1 §11, finding 5: competing Recommendations for the
    same open question are resolved via Contradiction, not silently
    overwritten."""
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    hypothesis = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="X"
    )
    belief = await belief_service.resolve_hypothesis(
        hypothesis.id, participant_id=investigator.id, belief_statement="Y", belief_confidence=0.5
    )

    recommendation_service = RecommendationService(db_session)
    planner = await get_or_create_agent_participant(db_session, "planner")

    r1 = await recommendation_service.propose(
        session.id,
        participant_id=investigator.id,
        statement="Check test coverage",
        target_belief_id=belief.id,
    )
    r2 = await recommendation_service.propose(
        session.id,
        participant_id=planner.id,
        statement="Check the dependency graph instead",
        target_belief_id=belief.id,
    )
    assert r1.id != r2.id

    contradictions, total = await ContradictionService(db_session).list_page(session.id)
    assert total == 1
    party_ids = {p.artifact_id for p in contradictions[0].parties}
    assert party_ids == {r1.id, r2.id}


async def test_identical_recommendation_statements_do_not_spuriously_conflict(
    db_session: AsyncSession,
) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    hypothesis = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="X"
    )
    belief = await belief_service.resolve_hypothesis(
        hypothesis.id, participant_id=investigator.id, belief_statement="Y", belief_confidence=0.5
    )
    recommendation_service = RecommendationService(db_session)

    await recommendation_service.propose(
        session.id,
        participant_id=investigator.id,
        statement="Check test coverage",
        target_belief_id=belief.id,
    )
    await recommendation_service.propose(
        session.id,
        participant_id=investigator.id,
        statement="Check test coverage",
        target_belief_id=belief.id,
    )
    _, total = await ContradictionService(db_session).list_page(session.id)
    assert total == 0


async def test_accept_recommendation_does_not_create_a_decision(
    db_session: AsyncSession,
) -> None:
    """Architecture v2.1 §5: accepting is not committing — that stays
    DecisionService's job alone."""
    session, investigator, _ = await _session_and_investigator(db_session)
    recommendation = await RecommendationService(db_session).propose(
        session.id, participant_id=investigator.id, statement="Do X"
    )
    accepted = await RecommendationService(db_session).accept(
        recommendation.id, participant_id=investigator.id
    )
    assert accepted.status == "accepted"

    decisions, total = await DecisionService(db_session).list_page(session.id)
    assert total == 0


# --- ContradictionService — N-ary + aggregate consistency ----------------


async def test_contradiction_requires_at_least_two_parties(db_session: AsyncSession) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    hypothesis = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="X"
    )
    belief = await belief_service.resolve_hypothesis(
        hypothesis.id, participant_id=investigator.id, belief_statement="Y", belief_confidence=0.5
    )

    with pytest.raises(ConflictError):
        await ContradictionService(db_session).detect(
            session.id,
            participant_id=investigator.id,
            description="Not really a dispute",
            party_artifact_ids=[belief.id],
        )


async def test_contradiction_supports_three_or_more_parties(db_session: AsyncSession) -> None:
    """Architecture v2.1 §2.2 (Δ v2.1): N-ary, not fixed at two."""
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    evidence_service = EvidenceService(db_session)

    h1 = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="A"
    )
    belief = await belief_service.resolve_hypothesis(
        h1.id, participant_id=investigator.id, belief_statement="B", belief_confidence=0.5
    )
    ev1 = await evidence_service.record(
        session.id,
        participant_id=investigator.id,
        evidence_kind="retrieved",
        summary="doc says X",
        source="confluence",
    )
    ev2 = await evidence_service.record(
        session.id,
        participant_id=investigator.id,
        evidence_kind="code_change",
        summary="code does Y",
        source="git",
    )

    contradiction = await ContradictionService(db_session).detect(
        session.id,
        participant_id=investigator.id,
        description="Three-way disagreement",
        party_artifact_ids=[belief.id, ev1.id, ev2.id],
    )
    assert len(contradiction.parties) == 3


async def test_contradiction_rejects_a_party_from_a_different_session(
    db_session: AsyncSession,
) -> None:
    """Architecture v2.1 §2.2 (Δ v2.1): ownership is "the narrowest scope
    containing every disputing party" — enforced here as aggregate
    consistency, since RFC-001 has no Mission to widen the scope to."""
    session_a, investigator, user = await _session_and_investigator(db_session)
    session_b = await SessionService(db_session).create_session(title="Other", created_by=user)

    belief_service = BeliefService(db_session)
    h_a = await belief_service.propose_hypothesis(
        session_a.id, participant_id=investigator.id, description="A"
    )
    belief_a = await belief_service.resolve_hypothesis(
        h_a.id,
        participant_id=investigator.id,
        belief_statement="A confirmed",
        belief_confidence=0.5,
    )
    h_b = await belief_service.propose_hypothesis(
        session_b.id, participant_id=investigator.id, description="B"
    )
    belief_b = await belief_service.resolve_hypothesis(
        h_b.id,
        participant_id=investigator.id,
        belief_statement="B confirmed",
        belief_confidence=0.5,
    )

    with pytest.raises(ConflictError):
        await ContradictionService(db_session).detect(
            session_a.id,
            participant_id=investigator.id,
            description="Cross-session — must be rejected",
            party_artifact_ids=[belief_a.id, belief_b.id],
        )


async def test_contradiction_rejects_a_nonexistent_party(db_session: AsyncSession) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    hypothesis = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="X"
    )
    belief = await belief_service.resolve_hypothesis(
        hypothesis.id, participant_id=investigator.id, belief_statement="Y", belief_confidence=0.5
    )

    with pytest.raises(NotFoundError):
        await ContradictionService(db_session).detect(
            session.id,
            participant_id=investigator.id,
            description="x",
            party_artifact_ids=[belief.id, uuid.uuid4()],
        )


async def test_resolve_contradiction_records_the_resolution(db_session: AsyncSession) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    h1 = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="A"
    )
    b1 = await belief_service.resolve_hypothesis(
        h1.id, participant_id=investigator.id, belief_statement="A confirmed", belief_confidence=0.5
    )
    h2 = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="B"
    )
    b2 = await belief_service.resolve_hypothesis(
        h2.id, participant_id=investigator.id, belief_statement="B confirmed", belief_confidence=0.5
    )
    contradiction_service = ContradictionService(db_session)
    contradiction = await contradiction_service.detect(
        session.id,
        participant_id=investigator.id,
        description="A vs B",
        party_artifact_ids=[b1.id, b2.id],
    )

    resolved = await contradiction_service.resolve(
        contradiction.id, participant_id=investigator.id, resolution_note="Trusted the code"
    )
    assert resolved.status == "resolved"
    assert resolved.resolution_note == "Trusted the code"

    with pytest.raises(ConflictError):
        await contradiction_service.resolve(
            contradiction.id, participant_id=investigator.id, resolution_note="again"
        )


async def test_mark_unresolved_is_distinct_from_resolved(db_session: AsyncSession) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)
    h1 = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="A"
    )
    b1 = await belief_service.resolve_hypothesis(
        h1.id, participant_id=investigator.id, belief_statement="A confirmed", belief_confidence=0.5
    )
    h2 = await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="B"
    )
    b2 = await belief_service.resolve_hypothesis(
        h2.id, participant_id=investigator.id, belief_statement="B confirmed", belief_confidence=0.5
    )
    contradiction = await ContradictionService(db_session).detect(
        session.id,
        participant_id=investigator.id,
        description="A vs B",
        party_artifact_ids=[b1.id, b2.id],
    )

    unresolved = await ContradictionService(db_session).mark_unresolved(
        contradiction.id, participant_id=investigator.id, note="Genuinely undecidable"
    )
    assert unresolved.status == "unresolved"


# --- DecisionService — the propose/commit boundary -----------------------


async def test_only_a_human_participant_may_commit_a_decision(db_session: AsyncSession) -> None:
    """Architecture v2.1 §5: "every agent can only propose; only a Human
    Participant... may commit." The single most important invariant in
    RFC-001."""
    session, investigator, user = await _session_and_investigator(db_session)

    with pytest.raises(ForbiddenError):
        await DecisionService(db_session).commit(
            session.id,
            committed_by_participant_id=investigator.id,
            decision_kind="planning_strategy",
            statement="Fix the merge logic",
            rationale="Traced root cause",
        )

    human = await get_or_create_human_participant(db_session, user)
    decision = await DecisionService(db_session).commit(
        session.id,
        committed_by_participant_id=human.id,
        decision_kind="planning_strategy",
        statement="Fix the merge logic",
        rationale="Traced root cause",
    )
    assert decision.committed_by_participant_id == human.id


async def test_decision_kind_is_validated(db_session: AsyncSession) -> None:
    session, _, user = await _session_and_investigator(db_session)
    human = await get_or_create_human_participant(db_session, user)
    with pytest.raises(ConflictError):
        await DecisionService(db_session).commit(
            session.id,
            committed_by_participant_id=human.id,
            decision_kind="not-a-real-kind",
            statement="x",
            rationale="y",
        )


async def test_commit_marks_the_source_recommendation_accepted(
    db_session: AsyncSession,
) -> None:
    session, investigator, user = await _session_and_investigator(db_session)
    recommendation = await RecommendationService(db_session).propose(
        session.id, participant_id=investigator.id, statement="Fix the merge logic"
    )
    human = await get_or_create_human_participant(db_session, user)

    await DecisionService(db_session).commit(
        session.id,
        committed_by_participant_id=human.id,
        decision_kind="planning_strategy",
        statement="Fix the merge logic",
        rationale="Traced root cause",
        recommendation_id=recommendation.id,
    )

    updated_recommendation = await RecommendationService(db_session).get(recommendation.id)
    assert updated_recommendation.status == "accepted"


async def test_supersede_creates_a_new_decision_and_keeps_the_old_one(
    db_session: AsyncSession,
) -> None:
    """Architecture v2.1 §2.2: "a change of mind is a new Decision that
    supersedes it, with the old one kept for history" — never edited."""
    session, _, user = await _session_and_investigator(db_session)
    human = await get_or_create_human_participant(db_session, user)
    decision_service = DecisionService(db_session)

    original = await decision_service.commit(
        session.id,
        committed_by_participant_id=human.id,
        decision_kind="planning_strategy",
        statement="Fix the merge logic",
        rationale="Traced root cause",
    )
    superseding = await decision_service.supersede(
        original.id,
        committed_by_participant_id=human.id,
        statement="Actually, fix the ingestion path",
        rationale="New evidence changed the picture",
    )

    assert superseding.id != original.id
    reloaded_original = await decision_service.get(original.id)
    assert reloaded_original.statement == "Fix the merge logic"  # never edited
    assert reloaded_original.superseded_by_decision_id == superseding.id


async def test_decision_service_exposes_no_update_or_delete(db_session: AsyncSession) -> None:
    decision_service = DecisionService(db_session)
    assert not hasattr(decision_service, "update")
    assert not hasattr(decision_service, "delete")


# --- TimelineService -----------------------------------------------------


async def test_timeline_entries_are_sequential_and_append_only(
    db_session: AsyncSession,
) -> None:
    session, investigator, _ = await _session_and_investigator(db_session)
    belief_service = BeliefService(db_session)

    await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="A"
    )
    await belief_service.propose_hypothesis(
        session.id, participant_id=investigator.id, description="B"
    )

    entries, total = await TimelineService(db_session).list_page(session.id)
    assert total == 3  # session_created + 2 hypothesis_proposed
    sequences = [e.sequence for e in entries]
    assert sequences == sorted(sequences)
    assert sequences == list(range(1, total + 1))


async def test_timeline_service_exposes_no_update_or_delete(db_session: AsyncSession) -> None:
    timeline_service = TimelineService(db_session)
    assert not hasattr(timeline_service, "update")
    assert not hasattr(timeline_service, "delete")
    assert not hasattr(timeline_service, "edit")
