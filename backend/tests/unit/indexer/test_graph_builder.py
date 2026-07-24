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
    PythonClass,
    PythonDependency,
    PythonFunction,
    PythonImport,
    PythonModule,
    SourceLocation,
)

LOCATION = SourceLocation(file_path="Order.java")
PY_LOCATION = SourceLocation(file_path="app/services/order_service.py")


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


def test_python_module_class_and_function_get_component_labels() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="app.services.order_service",
                package="app.services",
                location=PY_LOCATION,
                classes=[
                    PythonClass(
                        name="OrderService",
                        location=PY_LOCATION,
                        methods=[PythonFunction(name="create_order", location=PY_LOCATION)],
                    )
                ],
                functions=[PythonFunction(name="build_default_service", location=PY_LOCATION)],
            )
        ],
    )

    graph = build_graph("repo-1", model)
    node_labels = {node.id: node.labels for node in graph.nodes}

    module_id = "repo-1:module:app.services.order_service"
    class_id = "repo-1:class:app.services.order_service.OrderService"
    method_id = "repo-1:function:app.services.order_service.OrderService.create_order"
    function_id = "repo-1:function:app.services.order_service.build_default_service"

    assert node_labels[module_id] == ["Component", "Module"]
    assert node_labels[class_id] == ["Component", "Class"]
    assert node_labels[method_id] == ["Component", "Function"]
    assert node_labels[function_id] == ["Component", "Function"]

    edge_types = {(e.source_id, e.target_id, e.type) for e in graph.edges}
    assert ("repo-1:repository", module_id, "CONTAINS") in edge_types
    assert (module_id, class_id, "CONTAINS") in edge_types
    assert (class_id, method_id, "CONTAINS") in edge_types
    assert (module_id, function_id, "CONTAINS") in edge_types


def test_python_class_inheritance_edge_resolved_within_the_repository() -> None:
    base_location = SourceLocation(file_path="app/services/base_service.py")
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="app.services.base_service",
                package="app.services",
                location=base_location,
                classes=[PythonClass(name="BaseService", location=base_location)],
            ),
            PythonModule(
                name="app.services.order_service",
                package="app.services",
                location=PY_LOCATION,
                classes=[
                    PythonClass(name="OrderService", location=PY_LOCATION, bases=["BaseService"])
                ],
            ),
        ],
    )

    graph = build_graph("repo-1", model)
    edge_types = {(e.source_id, e.target_id, e.type) for e in graph.edges}
    child_id = "repo-1:class:app.services.order_service.OrderService"
    base_id = "repo-1:class:app.services.base_service.BaseService"
    assert (child_id, base_id, "INHERITS_FROM") in edge_types


def test_python_import_between_two_known_modules() -> None:
    other_location = SourceLocation(file_path="app/models/order.py")
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(name="app.models.order", package="app.models", location=other_location),
            PythonModule(
                name="app.services.order_service",
                package="app.services",
                location=PY_LOCATION,
                imports=[PythonImport(module="app.models.order", location=PY_LOCATION)],
            ),
        ],
    )

    graph = build_graph("repo-1", model)
    edge_types = {(e.source_id, e.target_id, e.type) for e in graph.edges}
    assert (
        "repo-1:module:app.services.order_service",
        "repo-1:module:app.models.order",
        "IMPORTS",
    ) in edge_types


def test_python_unresolved_import_produces_no_edge() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="app.services.order_service",
                package="app.services",
                location=PY_LOCATION,
                imports=[PythonImport(module="requests", location=PY_LOCATION)],
            )
        ],
    )

    graph = build_graph("repo-1", model)
    import_edges = [e for e in graph.edges if e.type == "IMPORTS"]
    assert import_edges == []


def test_python_call_graph_edge_for_unambiguous_bare_name() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="app.services.order_service",
                package="app.services",
                location=PY_LOCATION,
                functions=[
                    PythonFunction(name="caller", location=PY_LOCATION, calls=["helper"]),
                    PythonFunction(name="helper", location=PY_LOCATION),
                ],
            )
        ],
    )

    graph = build_graph("repo-1", model)
    edge_types = {(e.source_id, e.target_id, e.type) for e in graph.edges}
    caller_id = "repo-1:function:app.services.order_service.caller"
    helper_id = "repo-1:function:app.services.order_service.helper"
    assert (caller_id, helper_id, "CALLS") in edge_types


def test_python_call_graph_skips_ambiguous_bare_names() -> None:
    """Two unrelated functions/methods sharing a name must not produce a
    guessed CALLS edge to either one - see ADR 0007 precedent."""
    module_a = SourceLocation(file_path="app/a.py")
    module_b = SourceLocation(file_path="app/b.py")
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="app.a",
                package="app",
                location=module_a,
                classes=[
                    PythonClass(
                        name="A",
                        location=module_a,
                        methods=[PythonFunction(name="save", location=module_a)],
                    )
                ],
            ),
            PythonModule(
                name="app.b",
                package="app",
                location=module_b,
                classes=[
                    PythonClass(
                        name="B",
                        location=module_b,
                        methods=[PythonFunction(name="save", location=module_b)],
                    )
                ],
                functions=[
                    PythonFunction(name="caller", location=module_b, calls=["self.save"]),
                ],
            ),
        ],
    )

    graph = build_graph("repo-1", model)
    call_edges = [e for e in graph.edges if e.type == "CALLS"]
    assert call_edges == []


def test_python_dependency_depends_on_edge() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_dependencies=[PythonDependency(name="fastapi", version=">=0.100")],
    )

    graph = build_graph("repo-1", model)
    dependency_id = "repo-1:python-dependency:fastapi"
    assert any(node.id == dependency_id for node in graph.nodes)
    assert ("repo-1:repository", dependency_id, "DEPENDS_ON") in {
        (e.source_id, e.target_id, e.type) for e in graph.edges
    }
