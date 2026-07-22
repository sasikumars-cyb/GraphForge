"""Unit tests for AI analysis result schemas."""

import pytest
from pydantic import ValidationError

from app.ai.schemas.analysis_result import (
    AIAnalysisResult,
    BreakingChange,
    ConfidenceScore,
    DeploymentStep,
    MigrationAdvice,
    RegressionTest,
    ReleaseCoordinationPlan,
    RepositoryToNotify,
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


def test_deployment_step_valid() -> None:
    step = DeploymentStep(
        order=1,
        repository="order-service",
        action="Deploy first",
        reason="No other repository depends on it deploying later",
    )
    assert step.order == 1
    assert step.repository == "order-service"


def test_deployment_step_order_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        DeploymentStep(order=0, repository="order-service", action="Deploy", reason="Why")


def test_repository_to_notify_valid() -> None:
    notify = RepositoryToNotify(
        repository="inventory-service",
        reason="Consumes the order-created topic",
        urgency="blocking",
    )
    assert notify.repository == "inventory-service"
    assert notify.urgency == "blocking"


def test_repository_to_notify_rejects_arbitrary_urgency() -> None:
    with pytest.raises(ValidationError):
        RepositoryToNotify(repository="x", reason="y", urgency="ASAP")


def test_release_coordination_plan_empty_defaults() -> None:
    plan = ReleaseCoordinationPlan()
    assert plan.deployment_order == []
    assert plan.repositories_to_notify == []
    assert plan.rollout_strategy == ""
    assert plan.backward_compatibility_advice == ""
    assert plan.communication_summary == ""
    assert plan.rollout_risks == []


def test_release_coordination_plan_full() -> None:
    plan = ReleaseCoordinationPlan(
        deployment_order=[
            DeploymentStep(
                order=1, repository="order-service", action="Deploy first", reason="Producer"
            ),
            DeploymentStep(
                order=2,
                repository="inventory-service",
                action="Deploy after order-service",
                reason="Consumer",
            ),
        ],
        repositories_to_notify=[
            RepositoryToNotify(
                repository="inventory-service",
                reason="Consumes order-created",
                urgency="blocking",
            )
        ],
        rollout_strategy="Deploy order-service behind a feature flag first.",
        backward_compatibility_advice="Keep the event schema backward compatible.",
        communication_summary="Notify inventory team before rollout.",
        rollout_risks=["Kafka deserialization failures during rollout"],
    )
    assert len(plan.deployment_order) == 2
    assert plan.deployment_order[0].order == 1
    assert len(plan.repositories_to_notify) == 1
    assert plan.rollout_risks == ["Kafka deserialization failures during rollout"]


def test_release_coordination_plan_clears_single_repository_order() -> None:
    """A "deployment order" naming only one repository is a contradiction in
    terms - the model shouldn't have produced it, and it's cleared
    automatically regardless of whether the prompt was followed."""
    plan = ReleaseCoordinationPlan(
        deployment_order=[
            DeploymentStep(
                order=1, repository="order-service", action="Deploy", reason="Solo change"
            )
        ]
    )
    assert plan.deployment_order == []


def test_release_coordination_plan_keeps_multi_repository_order() -> None:
    plan = ReleaseCoordinationPlan(
        deployment_order=[
            DeploymentStep(order=1, repository="order-service", action="Deploy first", reason="A"),
            DeploymentStep(
                order=2, repository="inventory-service", action="Deploy after", reason="B"
            ),
        ]
    )
    assert len(plan.deployment_order) == 2


def test_grounded_in_strips_invented_repository() -> None:
    plan = ReleaseCoordinationPlan(
        deployment_order=[
            DeploymentStep(order=1, repository="order-service", action="Deploy first", reason="A"),
            DeploymentStep(order=2, repository="not-a-real-repo", action="Deploy", reason="B"),
        ]
    )
    grounded = plan.grounded_in({"order-service"}, "order-service")
    # Only one real repository remains, so the order is also cleared by the
    # same single-repository rule, re-applied on reconstruction.
    assert grounded.deployment_order == []


def test_grounded_in_strips_self_notification() -> None:
    plan = ReleaseCoordinationPlan(
        repositories_to_notify=[
            RepositoryToNotify(repository="order-service", reason="self", urgency="advisory"),
            RepositoryToNotify(repository="inventory-service", reason="real", urgency="blocking"),
        ]
    )
    grounded = plan.grounded_in({"order-service", "inventory-service"}, "order-service")
    assert [r.repository for r in grounded.repositories_to_notify] == ["inventory-service"]


def test_grounded_in_preserves_valid_multi_repository_plan() -> None:
    plan = ReleaseCoordinationPlan(
        deployment_order=[
            DeploymentStep(order=1, repository="order-service", action="Deploy first", reason="A"),
            DeploymentStep(
                order=2, repository="inventory-service", action="Deploy after", reason="B"
            ),
        ],
        rollout_strategy="Feature-flagged rollout.",
    )
    grounded = plan.grounded_in({"order-service", "inventory-service"}, "order-service")
    assert len(grounded.deployment_order) == 2
    assert grounded.rollout_strategy == "Feature-flagged rollout."


def test_ai_analysis_result_empty_defaults() -> None:
    result = AIAnalysisResult()
    assert result.executive_summary == ""
    assert result.breaking_changes == []
    assert result.migration_advice == []
    assert result.suggested_reviewers == []
    assert result.regression_tests == []
    assert result.release_coordination_plan == ReleaseCoordinationPlan()
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
        release_coordination_plan=ReleaseCoordinationPlan(
            rollout_strategy="Ship X on its own.",
        ),
        executive_summary="One breaking change in X",
        confidence=ConfidenceScore(score=0.88, reasoning="High signal"),
        prompt_version="1.0",
    )
    assert len(result.breaking_changes) == 1
    assert len(result.migration_advice) == 1
    assert len(result.suggested_reviewers) == 1
    assert len(result.regression_tests) == 1
    assert result.release_coordination_plan.rollout_strategy == "Ship X on its own."
    assert result.executive_summary == "One breaking change in X"
    assert result.confidence.score == 0.88
