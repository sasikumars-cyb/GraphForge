"""Unit tests for the Change Investigation Agent's shared dataclasses."""

from app.ai.agent.models import AgentState, Observation, ReasoningStep
from app.graph.models import GraphNode


def _node(node_id: str, *labels: str) -> GraphNode:
    return GraphNode(id=node_id, labels=["GraphNode", *labels])


def test_agent_state_defaults_are_empty() -> None:
    state = AgentState()
    assert state.changed_files == []
    assert state.pom_changed is False
    assert state.direct_nodes == []
    assert state.direct_service_nodes == []
    assert state.reasoning_log == []
    assert state.diff_content == ""
    assert state.recent_file_authors == {}


def test_direct_service_nodes_filters_to_component_labeled_nodes() -> None:
    state = AgentState()
    state.direct_nodes = [
        _node("1", "Controller", "Component"),
        _node("2", "MavenDependency"),
        _node("3", "Service", "Component"),
    ]
    assert {n.id for n in state.direct_service_nodes} == {"1", "3"}


def test_reasoning_step_records_a_skip_with_no_tool_selected() -> None:
    step = ReasoningStep(
        step_number=2,
        goal="Decide whether to traverse.",
        plan="Skip - no direct nodes.",
        tool_selected=None,
        observation=None,
        decision="Skipped.",
    )
    assert step.tool_selected is None
    assert step.observation is None


def test_observation_carries_a_summary_and_data() -> None:
    observation = Observation(
        tool_name="read_dependency_graph", summary="Matched 2 nodes.", data={"node_count": 2}
    )
    assert observation.tool_name == "read_dependency_graph"
    assert observation.data["node_count"] == 2
