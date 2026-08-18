"""Contract tests for `app.control_plane.observation_classification` —
Cap §16.2's fixed evaluation order. No database, no ControlPlane — a
pure function of explicit inputs.
"""

from __future__ import annotations

from app.control_plane.observation_classification import (
    ClassificationInputs,
    classify_observation,
)


def _inputs(**overrides: object) -> ClassificationInputs:
    defaults: dict[str, object] = {
        "outcome": "completed",
        "infrastructure_failure": False,
        "execution_context_mismatch": False,
        "prediction_result": "true",
    }
    defaults.update(overrides)
    return ClassificationInputs(**defaults)  # type: ignore[arg-type]


class TestFixedEvaluationOrder:
    def test_outcome_unknown_halts_before_classification(self) -> None:
        """Step 2 — NOT `uncertain_outcome` (Phase 5 design audit §8:
        'Do NOT assume outcome_unknown -> uncertain_outcome')."""
        result = classify_observation(
            _inputs(outcome="outcome_unknown", prediction_result="inconclusive")
        )
        assert result is None

    def test_infrastructure_failure_is_anomaly(self) -> None:
        result = classify_observation(_inputs(infrastructure_failure=True))
        assert result == "anomaly"

    def test_infrastructure_failure_wins_over_a_true_prediction(self) -> None:
        """Earlier steps win — an infra failure classifies as anomaly
        even when the (moot) Prediction would have evaluated true."""
        result = classify_observation(
            _inputs(infrastructure_failure=True, prediction_result="true")
        )
        assert result == "anomaly"

    def test_execution_context_mismatch_is_not_contradiction(self) -> None:
        """Step 4 — explicitly 'not Contradiction'; no context re-check
        mechanism exists, so evaluation halts (`None`), same shape as
        step 2's halt, never a five-way class."""
        result = classify_observation(
            _inputs(execution_context_mismatch=True, prediction_result="false")
        )
        assert result is None

    def test_context_mismatch_wins_over_infrastructure_failure_ordering(self) -> None:
        """Both step 3 and step 4 could independently apply; step 3
        precedes step 4 in the fixed order, so infra failure must win."""
        result = classify_observation(
            _inputs(infrastructure_failure=True, execution_context_mismatch=True)
        )
        assert result == "anomaly"

    def test_prediction_true_is_expected(self) -> None:
        assert classify_observation(_inputs(prediction_result="true")) == "expected"

    def test_prediction_false_is_contradiction(self) -> None:
        assert classify_observation(_inputs(prediction_result="false")) == "contradiction"

    def test_prediction_inconclusive_is_uncertain_outcome(self) -> None:
        assert classify_observation(_inputs(prediction_result="inconclusive")) == "uncertain_outcome"


class TestOldBehaviorWouldFail:
    """Each assertion here is the one that a naive/old implementation —
    collapsing outcome_unknown into uncertain_outcome, or classifying
    before checking infra failure — would fail (Phase 5 instructions
    §16)."""

    def test_outcome_unknown_is_not_silently_defaulted_to_a_five_way_class(self) -> None:
        result = classify_observation(_inputs(outcome="outcome_unknown"))
        # An old/naive implementation defaulting unset classification to
        # "uncertain_outcome" would fail this exact assertion.
        assert result is None
        assert result != "uncertain_outcome"
