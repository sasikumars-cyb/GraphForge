"""Investigation Intelligence observability — admin-only view into the raw
signal ADR 0021 Phase 1 has been collecting.

Deliberately *not* a decision surface: nothing here feeds back into
`engine.py`'s own reads (`repository_provider_preference`,
`recent_repeated_failure`) — this is read-only visibility so the signal's
quality can be judged by a human before Phase 2 lets it influence
retrieval more aggressively (see ADR 0021's own "Phase 1 does not change
`_select()`'s scoring formula beyond the one capped heuristic" framing).
Queries the two Investigation Intelligence tables directly, the same way
`calibration.py` queries `ConfidenceCalibration` directly rather than
through a service — both are read-only aggregation endpoints over
append-only tables, not through the write-oriented service those tables
also have.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.database.session import get_db_session
from app.models.investigation_intelligence import (
    InvestigationOutcomeRecord,
    InvestigationProviderEventRecord,
)
from app.models.user import User

router = APIRouter(prefix="/investigation-intelligence", tags=["investigation-intelligence"])

# Bounded reads, not a full-table scan — same retention-consciousness
# `InvestigationIntelligenceService`'s own `DEFAULT_LOOKBACK`/
# `DEFAULT_EVENT_LIMIT` already apply to the planner-facing reads (see
# ADR 0021 §5); this endpoint is a human dashboard, not a planning input,
# but "recent, bounded" is still the right default rather than an
# ever-growing full-history pull.
_DEFAULT_WINDOW_DAYS = 30
_MAX_ROWS = 5000

# A `(scope, capability, provider)` triple with at least this many
# failure/unavailable outcomes in the window is surfaced as a "repeated
# failure" group — the same pattern `recent_repeated_failure()` checks for
# one specific triple at read time, aggregated here across all of them so
# a human can see where a future ConfluenceProvider-style fix would matter
# most. Not tied to any specific time window shorter than the endpoint's
# own — the point is visibility, not reproducing the planner's own
# recency logic.
_REPEATED_FAILURE_THRESHOLD = 2

_LATENCY_BUCKETS: list[tuple[float, float, str]] = [
    (0, 200, "0-200ms"),
    (200, 1000, "200ms-1s"),
    (1000, 5000, "1s-5s"),
    (5000, float("inf"), "5s+"),
]

_CONFIDENCE_DELTA_BUCKETS: list[tuple[float, float, str]] = [
    # `_bucket()` matches `lo <= value < hi` uniformly across every bucket
    # list — a right-open convention that's correct for the other
    # (latency) buckets below, but would silently misfile an exact 0.0
    # delta into "0 - 0.05" rather than this bucket's own "<= 0" label if
    # the upper bound here were a bare 0.0. The tiny epsilon keeps exactly
    # 0.0 (`confidence_after == confidence_before`, the common "gathered
    # evidence but learned nothing new" case) inside the bucket its label
    # actually promises.
    (float("-inf"), 1e-9, "<= 0 (no improvement)"),
    (1e-9, 0.05, "0 - 0.05"),
    (0.05, 0.15, "0.05 - 0.15"),
    (0.15, 1.01, "0.15+"),
]


def _bucket(value: float, buckets: list[tuple[float, float, str]]) -> str:
    for lo, hi, label in buckets:
        if lo <= value < hi:
            return label
    return buckets[-1][2]


class OutcomeCount(BaseModel):
    outcome: str
    count: int


class ProviderStat(BaseModel):
    provider: str
    capability: str
    total: int
    success: int
    success_rate: float
    avg_latency_ms: float
    outcome_counts: list[OutcomeCount]


class DistributionBucket(BaseModel):
    bucket: str
    count: int


class CycleStat(BaseModel):
    terminal_outcome: str
    count: int
    avg_cycles: float


class PriorityBoostUsage(BaseModel):
    total_events: int
    # `priority_boost_applied != 0` — how often the (capped) heuristic
    # actually moved a score at all, regardless of source.
    boosted_events: int
    boost_usage_rate: float
    # `priority_boost_source in ("both", "memory_seeded")` — how often
    # Investigation Intelligence itself contributed to that boost, as
    # opposed to the live-LLM signal alone. This is the number that
    # answers "is the memory signal actually being read," independent of
    # whether it changed anything.
    memory_influenced_events: int
    memory_hit_rate: float


class RepeatedFailureGroup(BaseModel):
    scope_type: str
    scope_id: str
    capability: str
    provider: str
    failure_count: int
    most_recent_at: datetime


class InvestigationIntelligenceSummaryResponse(BaseModel):
    window_days: int
    total_provider_events: int
    total_investigations: int
    providers: list[ProviderStat]
    confidence_improvement_distribution: list[DistributionBucket]
    latency_distribution: list[DistributionBucket]
    cycles_by_terminal_outcome: list[CycleStat]
    priority_boost_usage: PriorityBoostUsage
    repeated_failure_groups: list[RepeatedFailureGroup]


@router.get("/summary", response_model=InvestigationIntelligenceSummaryResponse)
async def get_investigation_intelligence_summary(
    window_days: int = Query(default=_DEFAULT_WINDOW_DAYS, ge=1, le=365),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> InvestigationIntelligenceSummaryResponse:
    """Everything ADR 0021 Phase 1 asked for before Phase 2 is considered:
    are the collected signals real and sane — providers actually
    differentiating on success rate, confidence deltas actually
    distributed rather than clustered at zero, latency actually varying
    by provider, the boost heuristic actually firing and actually
    reading memory rather than defaulting to "none" every time, and
    repeated-failure patterns actually present in the data (the concrete
    thing a future ConfluenceProvider integration would act on)."""
    since = datetime.now(UTC) - timedelta(days=window_days)

    provider_events = list(
        (
            await db.execute(
                select(InvestigationProviderEventRecord)
                .where(InvestigationProviderEventRecord.created_at >= since)
                .order_by(InvestigationProviderEventRecord.sequence.desc())
                .limit(_MAX_ROWS)
            )
        )
        .scalars()
        .all()
    )
    outcomes = list(
        (
            await db.execute(
                select(InvestigationOutcomeRecord)
                .where(InvestigationOutcomeRecord.created_at >= since)
                .order_by(InvestigationOutcomeRecord.sequence.desc())
                .limit(_MAX_ROWS)
            )
        )
        .scalars()
        .all()
    )

    # -- provider outcome counts + success rate + latency --------------
    by_provider_capability: dict[tuple[str, str], list[InvestigationProviderEventRecord]] = defaultdict(
        list
    )
    for event in provider_events:
        by_provider_capability[(event.provider, event.capability)].append(event)

    providers = [
        ProviderStat(
            provider=provider,
            capability=capability,
            total=len(events),
            success=sum(1 for e in events if e.outcome == "success"),
            success_rate=round(sum(1 for e in events if e.outcome == "success") / len(events), 4),
            avg_latency_ms=round(sum(e.latency_ms for e in events) / len(events), 1),
            outcome_counts=[
                OutcomeCount(outcome=outcome, count=count)
                for outcome, count in sorted(
                    _count_by(events, lambda e: e.outcome).items(), key=lambda kv: kv[0]
                )
            ],
        )
        for (provider, capability), events in sorted(by_provider_capability.items())
    ]

    # -- confidence improvement distribution ----------------------------
    confidence_deltas = defaultdict(int)
    for event in provider_events:
        delta = event.confidence_after - event.confidence_before
        confidence_deltas[_bucket(delta, _CONFIDENCE_DELTA_BUCKETS)] += 1
    confidence_improvement_distribution = [
        DistributionBucket(bucket=label, count=confidence_deltas.get(label, 0))
        for *_ignored, label in _CONFIDENCE_DELTA_BUCKETS
        if confidence_deltas.get(label, 0) > 0
    ]

    # -- latency distribution --------------------------------------------
    latency_counts = defaultdict(int)
    for event in provider_events:
        latency_counts[_bucket(event.latency_ms, _LATENCY_BUCKETS)] += 1
    latency_distribution = [
        DistributionBucket(bucket=label, count=latency_counts.get(label, 0))
        for *_ignored, label in _LATENCY_BUCKETS
        if latency_counts.get(label, 0) > 0
    ]

    # -- investigation cycles, by terminal outcome ------------------------
    by_terminal: dict[str, list[InvestigationOutcomeRecord]] = defaultdict(list)
    for outcome_row in outcomes:
        by_terminal[outcome_row.terminal_outcome].append(outcome_row)
    cycles_by_terminal_outcome = [
        CycleStat(
            terminal_outcome=terminal,
            count=len(rows),
            avg_cycles=round(sum(r.cycles_used for r in rows) / len(rows), 2),
        )
        for terminal, rows in sorted(by_terminal.items())
    ]

    # -- priority boost usage + memory hit rate ---------------------------
    total_events = len(provider_events)
    boosted = sum(1 for e in provider_events if e.priority_boost_applied != 0)
    memory_influenced = sum(
        1 for e in provider_events if e.priority_boost_source in ("both", "memory_seeded")
    )
    priority_boost_usage = PriorityBoostUsage(
        total_events=total_events,
        boosted_events=boosted,
        boost_usage_rate=round(boosted / total_events, 4) if total_events else 0.0,
        memory_influenced_events=memory_influenced,
        memory_hit_rate=round(memory_influenced / total_events, 4) if total_events else 0.0,
    )

    # -- repeated failure detection ----------------------------------------
    by_triple: dict[tuple[str, str, str, str], list[InvestigationProviderEventRecord]] = defaultdict(
        list
    )
    for event in provider_events:
        if event.outcome in ("failed", "unavailable"):
            by_triple[(event.scope_type, event.scope_id, event.capability, event.provider)].append(
                event
            )
    repeated_failure_groups = [
        RepeatedFailureGroup(
            scope_type=scope_type,
            scope_id=scope_id,
            capability=capability,
            provider=provider,
            failure_count=len(events),
            most_recent_at=max(e.created_at for e in events),
        )
        for (scope_type, scope_id, capability, provider), events in by_triple.items()
        if len(events) >= _REPEATED_FAILURE_THRESHOLD
    ]
    repeated_failure_groups.sort(key=lambda g: g.failure_count, reverse=True)

    return InvestigationIntelligenceSummaryResponse(
        window_days=window_days,
        total_provider_events=total_events,
        total_investigations=len(outcomes),
        providers=providers,
        confidence_improvement_distribution=confidence_improvement_distribution,
        latency_distribution=latency_distribution,
        cycles_by_terminal_outcome=cycles_by_terminal_outcome,
        priority_boost_usage=priority_boost_usage,
        repeated_failure_groups=repeated_failure_groups,
    )


def _count_by(items: list, key) -> dict[str, int]:  # noqa: ANN001 - small local helper
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[key(item)] += 1
    return dict(counts)
