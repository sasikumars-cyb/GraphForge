"""Feedback aggregation, learning statistics, and trend detection — all
pure functions over an already-fetched list of persisted events. No
database access here; `app.learning_engine.service.LearningEngineService`
owns fetching, this module owns turning what was fetched into numbers.

Determinism/reproducibility (Phase 5's requirement): every function here
is a pure fold over its input list, with no dependency on wall-clock time,
randomness, or dict/set iteration order (all breakdowns are built by
iterating the input list itself, in the order it was passed, and any
grouping keys are read back out sorted) — the same `events` list always
produces the exact same `LearningStatistics`.

Future-ready without a redesign (Phase 4): every breakdown here is keyed
by `relationship_type` and/or `generator_names`, the two dimensions a
future validator-calibration, confidence-calibration, prompt-evolution, or
model-benchmarking RFC would need to slice by. Repository health scoring
is one `LearningStatistics` per repository (already the shape this
returns). Organization-wide learning is the same computation over the
union of multiple repositories' events — no new column, no schema change,
just a different set fed to `compute_statistics`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from app.learning_engine.contracts.learning_event import LearningEvent, LearningEventType

_REJECTION_EVENT_TYPES: frozenset[LearningEventType] = frozenset(
    {"rejected_relationship", "repeated_false_positive"}
)
_APPROVAL_EVENT_TYPES: frozenset[LearningEventType] = frozenset({"approved_relationship"})


@dataclass(frozen=True)
class RepeatedFalsePositiveSignal:
    """A derived, read-time-only signal — never itself persisted as a row.
    Fully reproducible from the stored `rejected_relationship` events
    alone, so storing it separately would be a duplicate of information
    already on disk (the same "single source of truth" discipline ADR
    0018 applies to `ConfidenceExplanation` vs. `ConfidenceModel`)."""

    relationship_type: str
    generator_name: str | None
    rejection_count: int


@dataclass(frozen=True)
class LearningStatistics:
    repository_id: str
    total_events: int
    counts_by_event_type: dict[str, int]
    counts_by_relationship_type: dict[str, dict[str, int]]
    approval_rate: float | None
    rejection_rate: float | None
    repeated_false_positive_signals: tuple[RepeatedFalsePositiveSignal, ...]
    trend_by_relationship_type: dict[str, str]
    computed_at: datetime


def compute_statistics(
    repository_id: str,
    events: list[LearningEvent],
    *,
    computed_at: datetime,
    false_positive_threshold: int = 3,
) -> LearningStatistics:
    counts_by_event_type: dict[str, int] = defaultdict(int)
    counts_by_relationship_type: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rejections_by_relationship_generator: dict[tuple[str, str | None], int] = defaultdict(int)

    for event in events:
        counts_by_event_type[event.event_type] += 1
        if event.relationship_type is not None:
            counts_by_relationship_type[event.relationship_type][event.event_type] += 1
        if event.event_type == "rejected_relationship" and event.relationship_type is not None:
            generator_names = event.generator_names or (None,)
            for generator_name in generator_names:
                rejections_by_relationship_generator[(event.relationship_type, generator_name)] += 1

    approvals = sum(counts_by_event_type.get(t, 0) for t in _APPROVAL_EVENT_TYPES)
    rejections = sum(counts_by_event_type.get(t, 0) for t in _REJECTION_EVENT_TYPES)
    judged = approvals + rejections
    approval_rate = approvals / judged if judged else None
    rejection_rate = rejections / judged if judged else None

    signals = tuple(
        RepeatedFalsePositiveSignal(
            relationship_type=relationship_type,
            generator_name=generator_name,
            rejection_count=count,
        )
        for (relationship_type, generator_name), count in sorted(
            rejections_by_relationship_generator.items(), key=lambda item: item[0]
        )
        if count >= false_positive_threshold
    )

    trend = _detect_trends(events)

    return LearningStatistics(
        repository_id=repository_id,
        total_events=len(events),
        counts_by_event_type=dict(sorted(counts_by_event_type.items())),
        counts_by_relationship_type={
            relationship_type: dict(sorted(breakdown.items()))
            for relationship_type, breakdown in sorted(counts_by_relationship_type.items())
        },
        approval_rate=approval_rate,
        rejection_rate=rejection_rate,
        repeated_false_positive_signals=signals,
        trend_by_relationship_type=trend,
        computed_at=computed_at,
    )


def _detect_trends(events: list[LearningEvent]) -> dict[str, str]:
    """Splits each relationship_type's judged (approve/reject) events,
    ordered exactly as given (the caller is responsible for passing them
    in a stable chronological order — `sequence` ascending), into an
    older and a newer half, and compares rejection rate between the two.
    Deterministic, no statistics beyond arithmetic, no ML — a threshold
    RFC over this module's own numbers, nothing more."""
    by_type: dict[str, list[LearningEvent]] = defaultdict(list)
    for event in events:
        if event.relationship_type is None:
            continue
        if event.event_type in _APPROVAL_EVENT_TYPES or event.event_type in {
            "rejected_relationship"
        }:
            by_type[event.relationship_type].append(event)

    trends: dict[str, str] = {}
    for relationship_type, judged in sorted(by_type.items()):
        if len(judged) < 4:
            trends[relationship_type] = "insufficient_data"
            continue
        midpoint = len(judged) // 2
        older, newer = judged[:midpoint], judged[midpoint:]
        older_rate = sum(1 for e in older if e.event_type == "rejected_relationship") / len(older)
        newer_rate = sum(1 for e in newer if e.event_type == "rejected_relationship") / len(newer)
        if newer_rate > older_rate:
            trends[relationship_type] = "increasing_rejection_rate"
        elif newer_rate < older_rate:
            trends[relationship_type] = "decreasing_rejection_rate"
        else:
            trends[relationship_type] = "stable"
    return trends
