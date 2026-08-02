"""ADR 0018 RFC-07 — `compute_statistics`: determinism, reproducibility,
repeated-false-positive detection, trend detection. Pure unit tests over
hand-built `LearningEvent` lists, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_engine.contracts.correction import CorrectionSource
from app.learning_engine.aggregation import compute_statistics
from app.learning_engine.contracts.learning_event import LearningEvent, LearningEventType

_SOURCE = CorrectionSource(kind="human", identity="user@example.com", trust_level=1.0)
_NOW = datetime.now(UTC)


def _event(
    event_type: LearningEventType,
    *,
    relationship_type: str | None = "CALLS_SERVICE",
    generator_names: tuple[str, ...] = (),
    suffix: str = "1",
) -> LearningEvent:
    relationship_key = (
        f"repo:1:{relationship_type}:a:b{suffix}" if relationship_type is not None else None
    )
    return LearningEvent(
        id=f"event:{suffix}",
        repository_id="repo:1",
        event_type=event_type,
        source=_SOURCE,
        detail="detail",
        created_at=_NOW,
        relationship_key=relationship_key,
        relationship_type=relationship_type,
        generator_names=generator_names,
    )


def test_empty_events_yield_zeroed_statistics_with_no_rates() -> None:
    stats = compute_statistics("repo:1", [], computed_at=_NOW)
    assert stats.total_events == 0
    assert stats.approval_rate is None
    assert stats.rejection_rate is None
    assert stats.repeated_false_positive_signals == ()


def test_approval_and_rejection_rate_computed_from_judged_events_only() -> None:
    events = [
        _event("approved_relationship", suffix="1"),
        _event("approved_relationship", suffix="2"),
        _event("rejected_relationship", suffix="3"),
        _event("weak_evidence", suffix="4"),  # not judged -- excluded from rate
    ]
    stats = compute_statistics("repo:1", events, computed_at=_NOW)
    assert stats.approval_rate == 2 / 3
    assert stats.rejection_rate == 1 / 3


def test_repeated_false_positive_signal_appears_once_threshold_reached() -> None:
    events = [
        _event(
            "rejected_relationship",
            generator_names=("frontier_llm_generator",),
            suffix=str(i),
        )
        for i in range(3)
    ]
    stats = compute_statistics("repo:1", events, computed_at=_NOW, false_positive_threshold=3)
    assert len(stats.repeated_false_positive_signals) == 1
    signal = stats.repeated_false_positive_signals[0]
    assert signal.relationship_type == "CALLS_SERVICE"
    assert signal.generator_name == "frontier_llm_generator"
    assert signal.rejection_count == 3


def test_repeated_false_positive_signal_absent_below_threshold() -> None:
    events = [
        _event("rejected_relationship", generator_names=("frontier_llm_generator",), suffix=str(i))
        for i in range(2)
    ]
    stats = compute_statistics("repo:1", events, computed_at=_NOW, false_positive_threshold=3)
    assert stats.repeated_false_positive_signals == ()


def test_statistics_deterministic_and_reproducible_across_repeated_calls() -> None:
    events = [
        _event("approved_relationship", suffix="1"),
        _event("rejected_relationship", suffix="2"),
        _event("rejected_relationship", suffix="3"),
    ]
    first = compute_statistics("repo:1", events, computed_at=_NOW)
    second = compute_statistics("repo:1", events, computed_at=_NOW)
    assert first == second


def test_trend_detection_reports_increasing_rejection_rate() -> None:
    events = [
        _event("approved_relationship", suffix="1"),
        _event("approved_relationship", suffix="2"),
        _event("rejected_relationship", suffix="3"),
        _event("rejected_relationship", suffix="4"),
    ]
    stats = compute_statistics("repo:1", events, computed_at=_NOW)
    assert stats.trend_by_relationship_type["CALLS_SERVICE"] == "increasing_rejection_rate"


def test_trend_detection_reports_insufficient_data_below_minimum() -> None:
    events = [
        _event("approved_relationship", suffix="1"),
        _event("rejected_relationship", suffix="2"),
    ]
    stats = compute_statistics("repo:1", events, computed_at=_NOW)
    assert stats.trend_by_relationship_type["CALLS_SERVICE"] == "insufficient_data"


def test_events_with_no_relationship_type_excluded_from_relationship_breakdown() -> None:
    events = [_event("missing_relationship", relationship_type=None, suffix="1")]
    stats = compute_statistics("repo:1", events, computed_at=_NOW)
    assert stats.counts_by_relationship_type == {}
    assert stats.counts_by_event_type == {"missing_relationship": 1}
