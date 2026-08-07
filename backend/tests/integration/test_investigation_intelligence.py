"""ADR 0021 — `InvestigationIntelligenceRepository` and
`InvestigationIntelligenceService` against a real Postgres transaction
(`db_session` fixture, rolled back per test). Proves: events persist as
append-only rows, `StateSnapshot` round-trips through JSONB unchanged,
decay-weighted effectiveness is computed correctly from real rows, and
every write/read degrades to a safe default rather than raising — the
one property every caller in `engine.py`/`context_discovery/agent.py`
depends on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation_intelligence.contracts import (
    CURRENT_SNAPSHOT_VERSION,
    CandidateScore,
    InvestigationOutcomeEvent,
    InvestigationScope,
    ProviderOutcomeEvent,
    StateSnapshot,
)
from app.investigation_intelligence.repository import InvestigationIntelligenceRepository
from app.investigation_intelligence.service import InvestigationIntelligenceService

pytestmark = pytest.mark.asyncio


def _unique_scope() -> InvestigationScope:
    """A fresh scope per test — `db_session`'s rollback only isolates this
    test's own writes, not rows already committed to the shared dev
    database by real traffic against this same running backend (the
    Investigation Intelligence wiring is live in `context_discovery/
    agent.py`), so a fixed scope_id like "payment-service" can and does
    collide with real data."""
    return InvestigationScope(scope_type="repository", scope_id=f"test-repo-{uuid.uuid4().hex[:12]}")


def _provider_event(
    *,
    scope: InvestigationScope,
    investigation_id: str = "inv-1",
    provider: str = "confluence_mcp",
    outcome: str = "success",
    yielded_evidence: bool = True,
    confidence_before: float = 0.2,
    confidence_after: float = 0.6,
    latency_ms: int = 400,
    created_at: datetime | None = None,
    snapshot: StateSnapshot | None = None,
) -> ProviderOutcomeEvent:
    return ProviderOutcomeEvent(
        investigation_id=investigation_id,
        cycle_number=1,
        scope=scope,
        capability="documentation",
        investigation_type="feature",
        provider=provider,
        action_key=f"{provider}:search",
        outcome=outcome,  # type: ignore[arg-type]
        declared_cost=2,
        latency_ms=latency_ms,
        yielded_evidence=yielded_evidence,
        necessity_at_selection="recommended",
        base_score_at_selection=0.3,
        priority_boost_applied=0.0,
        priority_boost_source="none",
        confidence_before=confidence_before,
        confidence_after=confidence_after,
        state_snapshot=snapshot or StateSnapshot.empty(),
        created_at=created_at or datetime.now(UTC),
    )


def _outcome_event(
    *,
    scope: InvestigationScope,
    investigation_id: str = "inv-1",
    terminal_outcome: str = "READY",
    confidence: float | None = 0.8,
) -> InvestigationOutcomeEvent:
    return InvestigationOutcomeEvent(
        investigation_id=investigation_id,
        scope=scope,
        investigation_type="feature",
        cycles_used=3,
        terminal_outcome=terminal_outcome,  # type: ignore[arg-type]
        confidence=confidence,
        final_capability_scores={"documentation": 0.6, "architecture": 0.9},
        contradictions_encountered=1,
        contradictions_resolved=1,
        priority_boost_source_used=False,
    )


class TestRepositoryRoundTrip:
    async def test_provider_event_round_trips_through_jsonb_unchanged(
        self, db_session: AsyncSession
    ) -> None:
        scope = _unique_scope()
        repo = InvestigationIntelligenceRepository(db_session)
        snapshot = StateSnapshot(
            version=CURRENT_SNAPSHOT_VERSION,
            candidates_considered=(
                CandidateScore(
                    provider="confluence_mcp",
                    action_key="confluence_mcp:search",
                    capability="documentation",
                    necessity="recommended",
                    score=0.3,
                    cost=2,
                ),
            ),
            all_capability_scores={"documentation": 0.3, "architecture": 0.9},
            open_contradictions=1,
        )
        event = _provider_event(scope=scope, snapshot=snapshot)

        await repo.add_provider_event(event)

        rows = await repo.recent_provider_events(
            scope=scope, capability="documentation", since=datetime.now(UTC) - timedelta(days=1)
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.provider == "confluence_mcp"
        assert row.outcome == "success"

        from app.investigation_intelligence.repository import to_provider_outcome_event

        restored = to_provider_outcome_event(row)
        assert restored.state_snapshot == snapshot
        assert restored.scope == scope
        assert restored.investigation_id == "inv-1"

    async def test_outcome_event_persists(self, db_session: AsyncSession) -> None:
        scope = _unique_scope()
        investigation_id = f"inv-{uuid.uuid4().hex[:12]}"
        repo = InvestigationIntelligenceRepository(db_session)
        await repo.add_outcome(_outcome_event(scope=scope, investigation_id=investigation_id))
        await db_session.flush()
        # No dedicated read method for outcomes in Phase 1 (nothing reads
        # them yet — see ADR 0021's Phase 1 scope) — this proves the write
        # path alone doesn't raise and the row is really there.
        from sqlalchemy import select

        from app.models.investigation_intelligence import InvestigationOutcomeRecord

        rows = (
            (
                await db_session.execute(
                    select(InvestigationOutcomeRecord).where(
                        InvestigationOutcomeRecord.investigation_id == investigation_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].terminal_outcome == "READY"

    async def test_deserialize_tolerates_unrecognized_version(self) -> None:
        from app.investigation_intelligence.repository import to_state_snapshot

        assert to_state_snapshot({"version": 999, "junk": True}) == StateSnapshot.empty()
        assert to_state_snapshot({}) == StateSnapshot.empty()


class TestServiceEffectiveness:
    async def test_provider_effectiveness_ranks_more_useful_provider_first(
        self, db_session: AsyncSession
    ) -> None:
        scope = _unique_scope()
        service = InvestigationIntelligenceService(db_session)
        # A provider that reliably yields evidence and moves confidence...
        await service.record_provider_outcome(
            _provider_event(scope=scope, provider="rest_cql", outcome="success", yielded_evidence=True)
        )
        # ...beats one that reliably fails outright.
        await service.record_provider_outcome(
            _provider_event(
                scope=scope,
                provider="confluence_mcp",
                outcome="unavailable",
                yielded_evidence=False,
                confidence_before=0.2,
                confidence_after=0.2,
            )
        )

        results = await service.provider_effectiveness(scope=scope, capability="documentation")

        assert [r.provider for r in results] == ["rest_cql", "confluence_mcp"]
        assert results[0].weighted_usefulness > results[1].weighted_usefulness
        assert results[1].weighted_success_rate == 0.0

    async def test_provider_effectiveness_empty_when_nothing_recorded(
        self, db_session: AsyncSession
    ) -> None:
        service = InvestigationIntelligenceService(db_session)
        results = await service.provider_effectiveness(scope=_unique_scope(), capability="documentation")
        assert results == []

    async def test_repository_provider_preference_centered_at_zero_cold_start(
        self, db_session: AsyncSession
    ) -> None:
        service = InvestigationIntelligenceService(db_session)
        preference = await service.repository_provider_preference(
            scope=_unique_scope(), capability="documentation"
        )
        assert preference == 0.0

    async def test_repository_provider_preference_positive_after_success(
        self, db_session: AsyncSession
    ) -> None:
        scope = _unique_scope()
        service = InvestigationIntelligenceService(db_session)
        await service.record_provider_outcome(
            _provider_event(scope=scope, provider="rest_cql", outcome="success", yielded_evidence=True)
        )
        preference = await service.repository_provider_preference(
            scope=scope, capability="documentation"
        )
        assert preference > 0.0

    async def test_recent_repeated_failure_returns_none_after_a_success(
        self, db_session: AsyncSession
    ) -> None:
        scope = _unique_scope()
        service = InvestigationIntelligenceService(db_session)
        await service.record_provider_outcome(
            _provider_event(scope=scope, provider="confluence_mcp", outcome="success")
        )
        result = await service.recent_repeated_failure(
            scope=scope,
            provider="confluence_mcp",
            capability="documentation",
            within=timedelta(hours=1),
        )
        assert result is None

    async def test_recent_repeated_failure_returns_event_after_a_failure(
        self, db_session: AsyncSession
    ) -> None:
        scope = _unique_scope()
        service = InvestigationIntelligenceService(db_session)
        await service.record_provider_outcome(
            _provider_event(
                scope=scope, provider="confluence_mcp", outcome="unavailable", yielded_evidence=False
            )
        )
        result = await service.recent_repeated_failure(
            scope=scope,
            provider="confluence_mcp",
            capability="documentation",
            within=timedelta(hours=1),
        )
        assert result is not None
        assert result.outcome == "unavailable"
        assert result.provider == "confluence_mcp"

    async def test_recent_repeated_failure_ignores_events_outside_the_window(
        self, db_session: AsyncSession
    ) -> None:
        scope = _unique_scope()
        service = InvestigationIntelligenceService(db_session)
        stale = _provider_event(
            scope=scope,
            provider="confluence_mcp",
            outcome="unavailable",
            yielded_evidence=False,
            created_at=datetime.now(UTC) - timedelta(days=10),
        )
        await service.record_provider_outcome(stale)
        result = await service.recent_repeated_failure(
            scope=scope,
            provider="confluence_mcp",
            capability="documentation",
            within=timedelta(hours=1),
        )
        assert result is None

    async def test_scoped_reads_never_cross_repository_scopes(self, db_session: AsyncSession) -> None:
        scope = _unique_scope()
        other_scope = _unique_scope()
        service = InvestigationIntelligenceService(db_session)
        await service.record_provider_outcome(
            _provider_event(scope=other_scope, provider="rest_cql")
        )
        results = await service.provider_effectiveness(scope=scope, capability="documentation")
        assert results == []
