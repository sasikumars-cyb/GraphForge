from app.analysis.models.impact import RiskLevel
from app.analysis.services.risk_classifier import classify_risk
from app.graph.models import GraphNode


def _node(*labels: str) -> GraphNode:
    return GraphNode(id=f"r1:{'-'.join(labels)}", labels=["GraphNode", *labels], properties={})


def test_no_directly_impacted_nodes_and_nothing_else_touched_is_low() -> None:
    risk = classify_risk([], pom_changed=False, topics_touched=False)
    assert risk == RiskLevel.LOW


def test_controller_change_is_medium() -> None:
    risk = classify_risk(
        [_node("Component", "Controller")], pom_changed=False, topics_touched=False
    )
    assert risk == RiskLevel.MEDIUM


def test_service_change_is_medium() -> None:
    risk = classify_risk([_node("Component", "Service")], pom_changed=False, topics_touched=False)
    assert risk == RiskLevel.MEDIUM


def test_generic_component_change_with_no_other_signal_is_low() -> None:
    risk = classify_risk([_node("Component")], pom_changed=False, topics_touched=False)
    assert risk == RiskLevel.LOW


def test_feign_client_change_is_high() -> None:
    risk = classify_risk(
        [_node("Component", "FeignClient")], pom_changed=False, topics_touched=False
    )
    assert risk == RiskLevel.HIGH


def test_pom_changed_is_high_even_with_no_directly_impacted_nodes() -> None:
    risk = classify_risk([], pom_changed=True, topics_touched=False)
    assert risk == RiskLevel.HIGH


def test_topics_touched_is_high_even_for_a_plain_component() -> None:
    risk = classify_risk([_node("Component")], pom_changed=False, topics_touched=True)
    assert risk == RiskLevel.HIGH


def test_high_trigger_overrides_medium_signal() -> None:
    risk = classify_risk([_node("Component", "Controller")], pom_changed=True, topics_touched=False)
    assert risk == RiskLevel.HIGH


def test_mixed_controller_and_feign_client_is_high() -> None:
    risk = classify_risk(
        [_node("Component", "Controller"), _node("Component", "FeignClient")],
        pom_changed=False,
        topics_touched=False,
    )
    assert risk == RiskLevel.HIGH
