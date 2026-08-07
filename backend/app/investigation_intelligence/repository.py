"""Data access for Investigation Intelligence — ADR 0021.

Mirrors `app.repositories.engineering_memory_repository.
EngineeringMemoryRepository`'s conventions exactly: a plain class over an
injected `AsyncSession`, writes use `db.add()` + `db.flush()` (never
`commit()` — the caller/service owns the transaction boundary), reads use
plain SQLAlchemy `select()`, no raw SQL.

The one boundary this repository owns that `EngineeringMemoryRepository`
doesn't need: serializing the typed `contracts.StateSnapshot` dataclass
to/from the `state_snapshot` JSONB column. Nothing above this module ever
sees a raw dict for that field.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation_intelligence.contracts import (
    CURRENT_SNAPSHOT_VERSION,
    CandidateScore,
    InvestigationOutcomeEvent,
    InvestigationScope,
    ProviderOutcomeEvent,
    StateSnapshot,
)
from app.models.investigation_intelligence import (
    InvestigationOutcomeRecord,
    InvestigationProviderEventRecord,
)


def _serialize_snapshot(snapshot: StateSnapshot) -> dict:
    return {
        "version": snapshot.version,
        "candidates_considered": [
            dataclasses.asdict(c) for c in snapshot.candidates_considered
        ],
        "all_capability_scores": snapshot.all_capability_scores,
        "open_contradictions": snapshot.open_contradictions,
    }


def _deserialize_snapshot(blob: dict) -> StateSnapshot:
    """Tolerates an older/unrecognized `version` gracefully — returns
    `StateSnapshot.empty()` rather than raising, the same tolerance
    `EngineeringMemoryRepository`'s own `schema_version` handling already
    has to have (see ADR 0021 §3a). A missing/malformed blob (e.g. a row
    from before this column existed) degrades the same way."""
    if not blob or blob.get("version") != CURRENT_SNAPSHOT_VERSION:
        return StateSnapshot.empty()
    try:
        return StateSnapshot(
            version=blob["version"],
            candidates_considered=tuple(
                CandidateScore(**c) for c in blob.get("candidates_considered", [])
            ),
            all_capability_scores=dict(blob.get("all_capability_scores", {})),
            open_contradictions=int(blob.get("open_contradictions", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return StateSnapshot.empty()


def _record_from_provider_event(event: ProviderOutcomeEvent) -> InvestigationProviderEventRecord:
    return InvestigationProviderEventRecord(
        investigation_id=event.investigation_id,
        cycle_number=event.cycle_number,
        scope_type=event.scope.scope_type,
        scope_id=event.scope.scope_id,
        capability=event.capability,
        investigation_type=event.investigation_type,
        provider=event.provider,
        action_key=event.action_key,
        outcome=event.outcome,
        declared_cost=event.declared_cost,
        latency_ms=event.latency_ms,
        yielded_evidence=event.yielded_evidence,
        necessity_at_selection=event.necessity_at_selection,
        base_score_at_selection=event.base_score_at_selection,
        priority_boost_applied=event.priority_boost_applied,
        priority_boost_source=event.priority_boost_source,
        confidence_before=event.confidence_before,
        confidence_after=event.confidence_after,
        state_snapshot=_serialize_snapshot(event.state_snapshot),
        created_at=event.created_at,
    )


def _record_from_outcome_event(outcome: InvestigationOutcomeEvent) -> InvestigationOutcomeRecord:
    return InvestigationOutcomeRecord(
        investigation_id=outcome.investigation_id,
        scope_type=outcome.scope.scope_type,
        scope_id=outcome.scope.scope_id,
        investigation_type=outcome.investigation_type,
        cycles_used=outcome.cycles_used,
        terminal_outcome=outcome.terminal_outcome,
        confidence=outcome.confidence,
        final_capability_scores=dict(outcome.final_capability_scores),
        contradictions_encountered=outcome.contradictions_encountered,
        contradictions_resolved=outcome.contradictions_resolved,
        priority_boost_source_used=outcome.priority_boost_source_used,
        created_at=outcome.created_at,
    )


class InvestigationIntelligenceRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- writes ----------------------------------------------------------

    async def add_provider_event(self, event: ProviderOutcomeEvent) -> None:
        self._db.add(_record_from_provider_event(event))
        await self._db.flush()

    async def add_outcome(self, outcome: InvestigationOutcomeEvent) -> None:
        self._db.add(_record_from_outcome_event(outcome))
        await self._db.flush()

    # -- reads -------------------------------------------------------------

    async def recent_provider_events(
        self,
        *,
        scope: InvestigationScope,
        capability: str,
        provider: str | None = None,
        since: datetime,
        limit: int = 200,
    ) -> list[InvestigationProviderEventRecord]:
        """Bounded, deliberately — the most recent `limit` events within
        `since`, never a full-table scan. See ADR 0021 §5: this is the
        same retention-consciousness KAN-24 had to retrofit onto
        Engineering Memory after the fact, applied from day one here."""
        stmt = (
            select(InvestigationProviderEventRecord)
            .where(
                InvestigationProviderEventRecord.scope_type == scope.scope_type,
                InvestigationProviderEventRecord.scope_id == scope.scope_id,
                InvestigationProviderEventRecord.capability == capability,
                InvestigationProviderEventRecord.created_at >= since,
            )
            .order_by(InvestigationProviderEventRecord.sequence.desc())
            .limit(limit)
        )
        if provider is not None:
            stmt = stmt.where(InvestigationProviderEventRecord.provider == provider)
        return list((await self._db.execute(stmt)).scalars().all())

    async def most_recent_provider_event(
        self,
        *,
        scope: InvestigationScope,
        capability: str,
        provider: str,
        within: timedelta,
    ) -> InvestigationProviderEventRecord | None:
        since = datetime.now(UTC) - within
        stmt = (
            select(InvestigationProviderEventRecord)
            .where(
                InvestigationProviderEventRecord.scope_type == scope.scope_type,
                InvestigationProviderEventRecord.scope_id == scope.scope_id,
                InvestigationProviderEventRecord.capability == capability,
                InvestigationProviderEventRecord.provider == provider,
                InvestigationProviderEventRecord.created_at >= since,
            )
            .order_by(InvestigationProviderEventRecord.sequence.desc())
            .limit(1)
        )
        return (await self._db.execute(stmt)).scalars().first()


def to_state_snapshot(blob: dict) -> StateSnapshot:
    """Exposed for the service layer's read path — same deserialization
    the write path's round-trip already relies on."""
    return _deserialize_snapshot(blob)


def to_provider_outcome_event(record: InvestigationProviderEventRecord) -> ProviderOutcomeEvent:
    """The reverse of `_record_from_provider_event` — the ORM row never
    escapes this module; every read-path caller gets this typed shape
    instead."""
    return ProviderOutcomeEvent(
        investigation_id=record.investigation_id,
        cycle_number=record.cycle_number,
        scope=InvestigationScope(scope_type=record.scope_type, scope_id=record.scope_id),
        capability=record.capability,
        investigation_type=record.investigation_type,
        provider=record.provider,
        action_key=record.action_key,
        outcome=record.outcome,
        declared_cost=record.declared_cost,
        latency_ms=record.latency_ms,
        yielded_evidence=record.yielded_evidence,
        necessity_at_selection=record.necessity_at_selection,
        base_score_at_selection=record.base_score_at_selection,
        priority_boost_applied=record.priority_boost_applied,
        priority_boost_source=record.priority_boost_source,
        confidence_before=record.confidence_before,
        confidence_after=record.confidence_after,
        state_snapshot=_deserialize_snapshot(record.state_snapshot),
        created_at=record.created_at,
    )
