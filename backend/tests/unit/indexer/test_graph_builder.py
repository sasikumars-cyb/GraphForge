"""`build_graph` turns an `ArchitectureModel` into a generic `GraphPayload` -
pure/deterministic, no Neo4j involved."""

from app.indexer.graph.builder import build_graph
from app.indexer.models.architecture import (
    ArchitectureModel,
    Controller,
    Endpoint,
    KafkaConsumerUsage,
    KafkaProducerUsage,
    MavenDependency,
    SourceLocation,
)

LOCATION = SourceLocation(file_path="Order.java")


def test_builds_repository_and_controller_nodes_with_contains_edge() -> None:
    model = ArchitectureModel(
        language="java",
        framework="spring-boot",
        controllers=[
            Controller(
                name="OrderController",
                package="com.example",
                base_path="/orders",
                location=LOCATION,
                endpoints=[
                    Endpoint(
                        http_method="GET", path="/orders", handler_method="list", location=LOCATION
                    )
                ],
            )
        ],
    )

    graph = build_graph("repo-1", model)

    node_labels = {node.id: node.labels for node in graph.nodes}
    controller_id = "repo-1:controller:com.example.OrderController"
    assert node_labels["repo-1:repository"] == ["Repository"]
    assert node_labels[controller_id] == ["Component", "Controller"]

    edge_types = {(e.source_id, e.target_id, e.type) for e in graph.edges}
    assert ("repo-1:repository", controller_id, "CONTAINS") in edge_types


def test_kafka_usage_on_an_undiscovered_class_gets_a_generic_component_node() -> None:
    model = ArchitectureModel(
        language="java",
        framework="spring-boot",
        kafka_producers=[
            KafkaProducerUsage(
                topic="order-created",
                class_name="OrderEventProducer",
                method_name="publish",
                location=LOCATION,
            )
        ],
        kafka_consumers=[
            KafkaConsumerUsage(
                topic="order-created",
                class_name="OrderEventListener",
                method_name="onEvent",
                location=LOCATION,
            )
        ],
    )

    graph = build_graph("repo-1", model)

    node_labels = {node.id: node.labels for node in graph.nodes}
    assert node_labels["repo-1:component:OrderEventProducer"] == ["Component"]
    assert node_labels["repo-1:component:OrderEventListener"] == ["Component"]

    edge_types = {(e.source_id, e.target_id, e.type) for e in graph.edges}
    topic_id = "repo-1:kafka-topic:order-created"
    assert ("repo-1:component:OrderEventProducer", topic_id, "PRODUCES_TO") in edge_types
    assert ("repo-1:component:OrderEventListener", topic_id, "CONSUMES_FROM") in edge_types


def test_duplicate_kafka_topic_nodes_are_deduplicated() -> None:
    model = ArchitectureModel(
        language="java",
        framework="spring-boot",
        kafka_producers=[
            KafkaProducerUsage(
                topic="shared-topic", class_name="A", method_name="publish", location=LOCATION
            ),
        ],
        kafka_consumers=[
            KafkaConsumerUsage(
                topic="shared-topic", class_name="B", method_name="onEvent", location=LOCATION
            ),
        ],
    )

    graph = build_graph("repo-1", model)

    topic_nodes = [n for n in graph.nodes if n.id == "repo-1:kafka-topic:shared-topic"]
    assert len(topic_nodes) == 1


def test_maven_dependency_depends_on_edge() -> None:
    model = ArchitectureModel(
        language="java",
        framework="spring-boot",
        maven_dependencies=[
            MavenDependency(group_id="org.example", artifact_id="lib", version="1.0", scope=None)
        ],
    )

    graph = build_graph("repo-1", model)

    dependency_id = "repo-1:dependency:org.example:lib"
    assert any(node.id == dependency_id for node in graph.nodes)
    assert ("repo-1:repository", dependency_id, "DEPENDS_ON") in {
        (e.source_id, e.target_id, e.type) for e in graph.edges
    }
