"""Unit tests for `AgentPlanner` - pure rule-based decisions, no I/O."""

from app.ai.agent.planner import AgentPlanner
from app.analysis.models.impact import RiskLevel


def test_should_traverse_graph_skips_when_no_direct_nodes() -> None:
    decision = AgentPlanner().should_traverse_graph(has_direct_nodes=False)
    assert decision.should_call is False
    assert "docs" in decision.reasoning.lower() or "nothing to traverse" in decision.reasoning


def test_should_traverse_graph_calls_when_direct_nodes_found() -> None:
    decision = AgentPlanner().should_traverse_graph(has_direct_nodes=True)
    assert decision.should_call is True


def test_should_check_indexing_information_only_when_no_direct_nodes() -> None:
    planner = AgentPlanner()
    assert planner.should_check_indexing_information(has_direct_nodes=True).should_call is False
    assert planner.should_check_indexing_information(has_direct_nodes=False).should_call is True


def test_should_retrieve_repository_metadata_only_with_cross_repo_impact() -> None:
    planner = AgentPlanner()
    assert (
        planner.should_retrieve_repository_metadata(has_cross_repository_impact=False).should_call
        is False
    )
    assert (
        planner.should_retrieve_repository_metadata(has_cross_repository_impact=True).should_call
        is True
    )


def test_should_read_diff_skips_on_low_risk() -> None:
    planner = AgentPlanner()
    assert planner.should_read_diff(risk=RiskLevel.LOW).should_call is False
    assert planner.should_read_diff(risk=RiskLevel.MEDIUM).should_call is True
    assert planner.should_read_diff(risk=RiskLevel.HIGH).should_call is True


def test_should_read_git_history_only_with_impacted_services() -> None:
    planner = AgentPlanner()
    assert planner.should_read_git_history(has_impacted_services=False).should_call is False
    assert planner.should_read_git_history(has_impacted_services=True).should_call is True


def test_decision_reasoning_is_never_empty() -> None:
    """Every decision must explain itself - the reasoning log depends on it."""
    planner = AgentPlanner()
    decisions = [
        planner.should_traverse_graph(has_direct_nodes=True),
        planner.should_traverse_graph(has_direct_nodes=False),
        planner.should_check_indexing_information(has_direct_nodes=True),
        planner.should_check_indexing_information(has_direct_nodes=False),
        planner.should_retrieve_repository_metadata(has_cross_repository_impact=True),
        planner.should_retrieve_repository_metadata(has_cross_repository_impact=False),
        planner.should_read_diff(risk=RiskLevel.LOW),
        planner.should_read_diff(risk=RiskLevel.HIGH),
        planner.should_read_git_history(has_impacted_services=True),
        planner.should_read_git_history(has_impacted_services=False),
    ]
    for decision in decisions:
        assert decision.reasoning.strip() != ""
