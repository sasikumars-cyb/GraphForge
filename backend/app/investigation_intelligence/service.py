"""`InvestigationIntelligenceService` — ADR 0021 §1: the only interface
anything outside this package ever touches. `app.context_pipeline.
reasoning.engine` receives an already-constructed instance (or `None`)
through `SessionContext`; it never imports `models.py`, never touches an
`AsyncSession` for this purpose, never sees an ORM row.

Every read method returns a plain typed dataclass
(`contracts.ProviderEffectiveness`) — never a raw query result. Every
write is fire-and-forget from the caller's point of view: this service
never raises out of a write into the reasoning loop (see `record_*`'s own
docstrings) — Investigation Intelligence is a side effect the loop's
correctness must never depend on.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation_intelligence.contracts import (
    InvestigationOutcomeEvent,
    InvestigationScope,
    ProviderEffectiveness,
    ProviderOutcomeEvent,
)
from app.investigation_intelligence.repository import (
    InvestigationIntelligenceRepository,
    to_provider_outcome_event,
)

logger = logging.getLogger(__name__)

# ADR 0021 §5 — a stale verdict from two half-lives ago contributes about
# a quarter the weight of one from today, so a permission gate that gets
# fixed (or a repository whose documentation improves) is reflected in
# the planner's own behavior within a few real investigations, never a
# manual reset.
HALF_LIFE_DAYS = 30.0

# ADR 0021 §5 — bounded reads, not a full-table scan on every planning
# cycle. Matches KAN-24's own retention precedent for Engineering Memory,
# applied here from day one instead of as a later patch.
DEFAULT_LOOKBACK = timedelta(days=90)
DEFAULT_EVENT_LIMIT = 200


def _decay_weight(age_days: float) -> float:
    return math.exp(-age_days / HALF_LIFE_DAYS)


class InvestigationIntelligenceService:
    def __init__(self, db: AsyncSession) -> None:
        self._repo = InvestigationIntelligenceRepository(db)

    # -- writes ------------------------------------------------------------

    async def record_provider_outcome(self, event: ProviderOutcomeEvent) -> None:
        """Never raises into the caller — a failure to persist a learning
        signal must not fail the investigation it was learning from. Logs
        and swallows, the same isolation `engine.py`'s own per-action
        `except Exception` already gives every investigator."""
        try:
            await self._repo.add_provider_event(event)
        except Exception:
            logger.exception(
                "investigation_intelligence_record_provider_outcome_failed "
                "investigation_id=%s provider=%s",
                event.investigation_id,
                event.provider,
            )

    async def record_investigation_outcome(self, outcome: InvestigationOutcomeEvent) -> None:
        try:
            await self._repo.add_outcome(outcome)
        except Exception:
            logger.exception(
                "investigation_intelligence_record_outcome_failed investigation_id=%s",
                outcome.investigation_id,
            )

    # -- reads ---------------------------------------------------------------

    async def provider_effectiveness(
        self,
        *,
        scope: InvestigationScope,
        capability: str,
        lookback: timedelta = DEFAULT_LOOKBACK,
    ) -> list[ProviderEffectiveness]:
        """Recency-weighted effectiveness per provider for `(scope,
        capability)`, most effective first. Empty list, never an
        exception, when nothing is known yet — a cold-start investigation
        looks identical to one where this service was never wired in at
        all, matching the "heuristic, not a decision" framing (ADR 0021
        §7): absence of a signal must never look like a negative one."""
        try:
            since = datetime.now(UTC) - lookback
            events = await self._repo.recent_provider_events(
                scope=scope, capability=capability, since=since, limit=DEFAULT_EVENT_LIMIT
            )
        except Exception:
            logger.exception(
                "investigation_intelligence_read_failed scope=%s capability=%s",
                scope,
                capability,
            )
            return []

        if not events:
            return []

        now = datetime.now(UTC)
        by_provider: dict[str, list] = {}
        for event in events:
            by_provider.setdefault(event.provider, []).append(event)

        results: list[ProviderEffectiveness] = []
        for provider, provider_events in by_provider.items():
            total_weight = 0.0
            success_weight = 0.0
            usefulness_weight_sum = 0.0
            latency_weight_sum = 0.0
            latency_weighted_total = 0.0
            most_recent_at = None
            for record in provider_events:
                created_at = record.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                age_days = max((now - created_at).total_seconds() / 86400.0, 0.0)
                weight = _decay_weight(age_days)
                total_weight += weight

                if record.outcome == "success":
                    success_weight += weight

                # Usefulness — derived, never stored (ADR 0021 §3): a
                # deterministic blend of whether the action yielded
                # evidence at all and how much it moved the needle.
                confidence_delta = max(record.confidence_after - record.confidence_before, 0.0)
                useful = (1.0 if record.yielded_evidence else 0.0) * 0.5 + min(
                    confidence_delta, 1.0
                ) * 0.5
                usefulness_weight_sum += weight * useful

                latency_weight_sum += weight
                latency_weighted_total += weight * record.latency_ms

                if most_recent_at is None or created_at > most_recent_at:
                    most_recent_at = created_at

            if total_weight <= 0:
                continue

            results.append(
                ProviderEffectiveness(
                    provider=provider,
                    capability=capability,
                    weighted_success_rate=round(success_weight / total_weight, 4),
                    weighted_usefulness=round(usefulness_weight_sum / total_weight, 4),
                    average_latency_ms=round(latency_weighted_total / latency_weight_sum, 1)
                    if latency_weight_sum > 0
                    else 0.0,
                    sample_count=len(provider_events),
                    most_recent_at=most_recent_at,
                )
            )

        results.sort(key=lambda r: r.weighted_usefulness, reverse=True)
        return results

    async def repository_provider_preference(
        self,
        *,
        scope: InvestigationScope,
        capability: str,
        lookback: timedelta = DEFAULT_LOOKBACK,
    ) -> float:
        """The single scalar ADR 0021 §7's heuristic adds into
        `investigation_priority` for one capability — the effectiveness-
        weighted mean of `weighted_usefulness` across every provider known
        for `(scope, capability)`, centered at 0.5 so "no data yet" and
        "historically unremarkable" both contribute ~0 (never mistaken for
        a negative signal), positive when this capability has reliably
        been worth investigating for this scope, negative when it has
        reliably not. `_select()`'s own scoring treats a positive value as
        "prioritize this capability sooner" (see `engine._select`'s
        `adjusted_score = score - boost` — lower wins, so a positive boost
        lowers a capability's effective score, the weakest-first rule
        picking it up earlier). The caller (`engine.py`) applies its own
        hard cap (±0.15) — this method returns the raw signal, unclamped,
        so that cap stays the one place the ceiling is decided, matching
        `_select()`'s own scoring formula rule: this is a hint, never a
        decision.

        `0.0` — never an exception — when nothing is known yet, matching
        `provider_effectiveness`'s own cold-start guarantee.
        """
        effectiveness = await self.provider_effectiveness(
            scope=scope, capability=capability, lookback=lookback
        )
        if not effectiveness:
            return 0.0
        total_weight = sum(e.sample_count for e in effectiveness)
        if total_weight <= 0:
            return 0.0
        weighted_mean = (
            sum(e.weighted_usefulness * e.sample_count for e in effectiveness) / total_weight
        )
        return round(weighted_mean - 0.5, 4)

    async def recent_repeated_failure(
        self,
        *,
        scope: InvestigationScope,
        provider: str,
        capability: str,
        within: timedelta,
    ) -> ProviderOutcomeEvent | None:
        """The most recent event for `(scope, capability, provider)`
        within `within`, if it was a failure — `None` if nothing recent
        exists, or the most recent event was actually a success (this
        method answers "is there a recent failure to know about", not
        "what happened most recently regardless of outcome"). The
        concrete "skip MCP, use REST immediately" signal from ADR 0021's
        own worked example: a caller checks this before attempting a
        provider known to have just failed for the exact same scope and
        capability. `None` on any read failure too, never raises — a
        memory outage degrades to "nothing known," never to blocking the
        investigation."""
        try:
            record = await self._repo.most_recent_provider_event(
                scope=scope, capability=capability, provider=provider, within=within
            )
        except Exception:
            logger.exception(
                "investigation_intelligence_read_failed scope=%s provider=%s", scope, provider
            )
            return None
        if record is None or record.outcome not in ("unavailable", "failed"):
            return None
        return to_provider_outcome_event(record)
