from app.analysis.models.impact import display_name, impacted_node_from_graph_node, primary_label
from app.graph.models import GraphNode


def test_primary_label_prefers_controller_over_component() -> None:
    node = GraphNode(
        id="r1:controller:x",
        labels=["GraphNode", "Component", "Controller"],
        properties={},
    )
    assert primary_label(node) == "Controller"


def test_primary_label_falls_back_to_any_non_base_label() -> None:
    node = GraphNode(id="r1:x", labels=["GraphNode", "SomethingUnlisted"], properties={})
    assert primary_label(node) == "SomethingUnlisted"


def test_display_name_uses_name_property_for_components() -> None:
    node = GraphNode(
        id="r1:service:x",
        labels=["GraphNode", "Component", "Service"],
        properties={"name": "OrderService"},
    )
    assert display_name(node) == "OrderService"


def test_display_name_combines_method_and_path_for_endpoints() -> None:
    node = GraphNode(
        id="r1:endpoint:x",
        labels=["GraphNode", "Endpoint"],
        properties={"http_method": "GET", "path": "/orders/{id}"},
    )
    assert display_name(node) == "GET /orders/{id}"


def test_display_name_combines_group_and_artifact_for_dependencies() -> None:
    node = GraphNode(
        id="r1:dependency:x",
        labels=["GraphNode", "MavenDependency"],
        properties={
            "group_id": "org.springframework.boot",
            "artifact_id": "spring-boot-starter-web",
        },
    )
    assert display_name(node) == "org.springframework.boot:spring-boot-starter-web"


def test_display_name_falls_back_to_id_when_no_name_property() -> None:
    node = GraphNode(id="r1:mystery:x", labels=["GraphNode", "Component"], properties={})
    assert display_name(node) == "r1:mystery:x"


def test_impacted_node_from_graph_node_uses_the_nodes_own_repository_id() -> None:
    node = GraphNode(
        id="r1:service:x",
        labels=["GraphNode", "Component", "Service"],
        properties={"name": "OrderService", "repository_id": "r1"},
    )
    impacted = impacted_node_from_graph_node(node)
    assert impacted.id == "r1:service:x"
    assert impacted.name == "OrderService"
    assert impacted.node_type == "Service"
    assert impacted.repository_id == "r1"


def test_impacted_node_from_graph_node_accepts_repository_id_override() -> None:
    node = GraphNode(
        id="r2:service:x",
        labels=["GraphNode", "Component", "Service"],
        properties={"name": "PaymentService", "repository_id": "r2"},
    )
    impacted = impacted_node_from_graph_node(node, repository_id="r2")
    assert impacted.repository_id == "r2"
