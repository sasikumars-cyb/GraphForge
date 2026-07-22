"""Unit tests for AI analysis result schemas."""

import pytest
from pydantic import ValidationError

from app.ai.schemas.analysis_result import (
    AIAnalysisResult,
    BreakingChange,
    ConfidenceScore,
    MigrationAdvice,
    RegressionTest,
    SuggestedReviewer,
)


def test_confidence_score_valid() -> None:
    score = ConfidenceScore(score=0.85, reasoning="High overlap with owner")
    assert score.score == 0.85
    assert score.reasoning == "High overlap with owner"


def test_confidence_score_bounds_low() -> None:
    score = ConfidenceScore(score=0.0)
    assert score.score == 0.0


def test_confidence_score_bounds_high() -> None:
    score = ConfidenceScore(score=1.0)
    assert score.score == 1.0


def test_confidence_score_below_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        ConfidenceScore(score=-0.1)


def test_confidence_score_above_one_rejected() -> None:
    with pytest.raises(ValidationError):
        ConfidenceScore(score=1.1)


def test_breaking_change_valid() -> None:
    bc = BreakingChange(
        component="OrderService",
        description="Removed deprecated endpoint",
        severity="high",
        confidence=ConfidenceScore(score=0.9, reasoning="Direct removal"),
    )
    assert bc.component == "OrderService"
    assert bc.severity == "high"


def test_breaking_change_empty_component_rejected() -> None:
    with pytest.raises(ValidationError):
        BreakingChange(
            component="",
            description="Something",
            severity="high",
            confidence=ConfidenceScore(score=0.5),
        )


def test_migration_advice_valid() -> None:
    ma = MigrationAdvice(
        component="PaymentGateway",
        advice="Update client SDK to v3",
        priority="high",
    )
    assert ma.component == "PaymentGateway"
    assert ma.priority == "high"


def test_suggested_reviewer_valid() -> None:
    sr = SuggestedReviewer(
        reviewer="backend-team",
        reason="Owns the affected service",
        confidence=ConfidenceScore(score=0.75),
    )
    assert sr.reviewer == "backend-team"


def test_regression_test_valid() -> None:
    rt = RegressionTest(
        component="AuthController",
        test_description="Verify login still works after token change",
        priority="critical",
        confidence=ConfidenceScore(score=0.95, reasoning="Direct dependency"),
    )
    assert rt.component == "AuthController"
    assert rt.priority == "critical"


def test_ai_analysis_result_empty_defaults() -> None:
    result = AIAnalysisResult()
    assert result.executive_summary == ""
    assert result.breaking_changes == []
    assert result.migration_advice == []
    assert result.suggested_reviewers == []
    assert result.regression_tests == []
    assert result.confidence.score == 0.0
    assert result.prompt_version == ""


def test_ai_analysis_result_full() -> None:
    result = AIAnalysisResult(
        breaking_changes=[
            BreakingChange(
                component="X",
                description="Removed Y",
                severity="high",
                confidence=ConfidenceScore(score=0.9),
            )
        ],
        migration_advice=[MigrationAdvice(component="X", advice="Use Z instead", priority="high")],
        suggested_reviewers=[
            SuggestedReviewer(
                reviewer="alice",
                reason="Owns X",
                confidence=ConfidenceScore(score=0.8),
            )
        ],
        regression_tests=[
            RegressionTest(
                component="X",
                test_description="Test X still works",
                priority="high",
                confidence=ConfidenceScore(score=0.85),
            )
        ],
        executive_summary="One breaking change in X",
        confidence=ConfidenceScore(score=0.88, reasoning="High signal"),
        prompt_version="1.0",
    )
    assert len(result.breaking_changes) == 1
    assert len(result.migration_advice) == 1
    assert len(result.suggested_reviewers) == 1
    assert len(result.regression_tests) == 1
    assert result.executive_summary == "One breaking change in X"
    assert result.confidence.score == 0.88
