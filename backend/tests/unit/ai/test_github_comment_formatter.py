"""Unit tests for `format_review_comment` - pure markdown formatting, no
I/O, no DB, no HTTP. Verifies every section renders for a fully-populated
result, and that each independently-empty section falls back to a
predictable "None."/explanatory placeholder rather than disappearing."""

from __future__ import annotations

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
from app.ai.services.github_comment_formatter import format_review_comment

_DIRECT_SERVICES = [{"id": "1", "name": "order-service", "node_type": "Component"}]
_INDIRECT_SERVICES = [{"id": "2", "name": "payment-service", "node_type": "Component"}]


def _full_result() -> AIAnalysisResult:
    return AIAnalysisResult(
        executive_summary="Renames the order.cancelled Kafka topic.",
        breaking_changes=[
            BreakingChange(
                component="OrderEventProducer",
                description="Kafka topic name changed",
                severity="high",
                confidence=ConfidenceScore(score=0.9, reasoning="Topic constant renamed"),
            ),
        ],
        migration_advice=[
            MigrationAdvice(
                component="OrderEventProducer",
                advice="Update all consumers to use the new topic name",
                priority="high",
            ),
        ],
        suggested_reviewers=[
            SuggestedReviewer(
                reviewer="alice",
                reason="Primary owner of messaging infrastructure",
                confidence=ConfidenceScore(score=0.85, reasoning="Commit history analysis"),
            ),
        ],
        regression_tests=[
            RegressionTest(
                component="OrderEventProducer",
                test_description="Verify event delivery on the new topic",
                priority="high",
                confidence=ConfidenceScore(score=0.8, reasoning="Critical path"),
            ),
        ],
        release_coordination_plan=ReleaseCoordinationPlan(
            deployment_order=[
                DeploymentStep(
                    order=1,
                    repository="order-service",
                    action="Deploy first",
                    reason="Publishes the renamed topic",
                ),
                DeploymentStep(
                    order=2,
                    repository="payment-service",
                    action="Deploy second",
                    reason="Consumes the renamed topic",
                ),
            ],
            repositories_to_notify=[
                RepositoryToNotify(
                    repository="payment-service",
                    reason="Consumes order.cancelled",
                    urgency="blocking",
                ),
            ],
            rollout_strategy="Ship behind a feature flag.",
            backward_compatibility_advice="Keep the old topic alive for one release.",
            communication_summary="Notify #payments before merging.",
            rollout_risks=["Kafka deserialization failures during rollout"],
        ),
        confidence=ConfidenceScore(score=0.88, reasoning="High confidence analysis"),
        prompt_version="1.0.0",
    )


def test_full_result_renders_every_section() -> None:
    comment = format_review_comment(
        ai_result=_full_result(),
        risk="HIGH",
        directly_impacted_services=_DIRECT_SERVICES,
        indirectly_impacted_services=_INDIRECT_SERVICES,
    )

    assert comment.startswith("# 🤖 GraphForge AI Review")
    assert "## Summary" in comment
    assert "Renames the order.cancelled Kafka topic." in comment
    assert "## Risk" in comment
    assert "**HIGH**" in comment
    assert "## Breaking Changes" in comment
    assert "**OrderEventProducer** (high): Kafka topic name changed" in comment
    assert "confidence: 90%" in comment
    assert "## Migration Advice" in comment
    assert "Update all consumers to use the new topic name" in comment
    assert "## Impacted Services" in comment
    assert "**Directly impacted:** order-service" in comment
    assert "**Indirectly impacted (cross-repository):** payment-service" in comment
    assert "## Suggested Reviewers" in comment
    assert "@alice" in comment
    assert "confidence: 85%" in comment
    assert "## Recommended Regression Tests" in comment
    assert "Verify event delivery on the new topic" in comment
    assert "## Release Plan" in comment
    assert "1. **order-service** — Deploy first" in comment
    assert "2. **payment-service** — Deploy second" in comment
    assert "🔴 blocking" in comment
    assert "Ship behind a feature flag." in comment
    assert "Keep the old topic alive for one release." in comment
    assert "Notify #payments before merging." in comment
    assert "Kafka deserialization failures during rollout" in comment
    assert comment.rstrip().endswith(
        "Generated by GraphForge AI · prompt `1.0.0` · confidence 88% · "
        "this review was published from a previously computed analysis, not a new LLM call."
    )
    assert "---" in comment


def test_empty_breaking_changes_renders_none() -> None:
    result = _full_result().model_copy(update={"breaking_changes": []})
    comment = format_review_comment(
        ai_result=result,
        risk="LOW",
        directly_impacted_services=[],
        indirectly_impacted_services=[],
    )
    assert "No breaking changes identified." in comment


def test_empty_migration_advice_renders_placeholder() -> None:
    result = _full_result().model_copy(update={"migration_advice": []})
    comment = format_review_comment(
        ai_result=result,
        risk="LOW",
        directly_impacted_services=[],
        indirectly_impacted_services=[],
    )
    assert "No migration advice provided." in comment


def test_empty_suggested_reviewers_renders_placeholder() -> None:
    result = _full_result().model_copy(update={"suggested_reviewers": []})
    comment = format_review_comment(
        ai_result=result,
        risk="LOW",
        directly_impacted_services=[],
        indirectly_impacted_services=[],
    )
    assert "No reviewers suggested." in comment


def test_empty_regression_tests_renders_placeholder() -> None:
    result = _full_result().model_copy(update={"regression_tests": []})
    comment = format_review_comment(
        ai_result=result,
        risk="LOW",
        directly_impacted_services=[],
        indirectly_impacted_services=[],
    )
    assert "No regression tests suggested." in comment


def test_empty_impacted_services_render_none() -> None:
    comment = format_review_comment(
        ai_result=_full_result(),
        risk="LOW",
        directly_impacted_services=[],
        indirectly_impacted_services=[],
    )
    assert "**Directly impacted:** None." in comment
    assert "**Indirectly impacted (cross-repository):** None." in comment


def test_default_release_plan_renders_none_placeholders() -> None:
    result = _full_result().model_copy(
        update={"release_coordination_plan": ReleaseCoordinationPlan()}
    )
    comment = format_review_comment(
        ai_result=result,
        risk="LOW",
        directly_impacted_services=[],
        indirectly_impacted_services=[],
    )
    assert "No deployment order needed - single repository change." in comment
    assert "None." in comment
    assert "Not specified." in comment
    assert "None identified." in comment


def test_unknown_risk_and_empty_impacted_services_for_no_deterministic_analysis() -> None:
    comment = format_review_comment(
        ai_result=AIAnalysisResult(),
        risk="UNKNOWN",
        directly_impacted_services=[],
        indirectly_impacted_services=[],
    )
    assert "**UNKNOWN**" in comment
    assert "**Directly impacted:** None." in comment
