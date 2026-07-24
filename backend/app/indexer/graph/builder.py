"""Turns an `ArchitectureModel` into the generic `GraphPayload`
`app.graph` persists to Neo4j — the one place that knows how a discovered
Java entity maps to a graph label and relationship type.

Node id scheme: every id is namespaced `f"{repository_id}:{kind}:{key}"`,
so re-indexing the same repository always produces the same ids (MERGE
upserts in place) and ids never collide across repositories.
"""

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.indexer.models.architecture import ArchitectureModel, PythonFunction


def _repository_node_id(repository_id: str) -> str:
    return f"{repository_id}:repository"


def _controller_node_id(repository_id: str, package: str, name: str) -> str:
    return f"{repository_id}:controller:{package}.{name}"


def _service_node_id(repository_id: str, package: str, name: str) -> str:
    return f"{repository_id}:service:{package}.{name}"


def _feign_client_node_id(repository_id: str, package: str, name: str) -> str:
    return f"{repository_id}:feign:{package}.{name}"


def _generic_component_node_id(repository_id: str, class_name: str) -> str:
    return f"{repository_id}:component:{class_name}"


def _endpoint_node_id(owner_id: str, http_method: str, path: str, handler_method: str) -> str:
    return f"{owner_id}:endpoint:{http_method}:{path}:{handler_method}"


def _kafka_topic_node_id(repository_id: str, topic: str) -> str:
    return f"{repository_id}:kafka-topic:{topic}"


def _dependency_node_id(repository_id: str, group_id: str, artifact_id: str) -> str:
    return f"{repository_id}:dependency:{group_id}:{artifact_id}"


def _module_node_id(repository_id: str, module_name: str) -> str:
    return f"{repository_id}:module:{module_name}"


def _class_node_id(repository_id: str, module_name: str, class_name: str) -> str:
    return f"{repository_id}:class:{module_name}.{class_name}"


def _function_node_id(repository_id: str, qualified_name: str) -> str:
    return f"{repository_id}:function:{qualified_name}"


def _python_dependency_node_id(repository_id: str, name: str) -> str:
    return f"{repository_id}:python-dependency:{name}"


def _build_python_graph(
    repository_id: str,
    repo_id: str,
    model: ArchitectureModel,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> None:
    """Python modules/classes/functions map onto the exact same `Component`
    label the Java parser already uses (plus a specific secondary label,
    same pattern as `Controller`/`Service`/`FeignClient`) - so a Python
    Component and a Java Component are indistinguishable to callers of the
    graph, e.g. Planning's queries, once indexed.
    """
    module_node_id_by_name = {
        m.name: _module_node_id(repository_id, m.name) for m in model.python_modules
    }

    # Bare function/method name -> node id, but only when the name is
    # unambiguous across the whole repository. An ambiguous bare name (the
    # same method name on two unrelated classes) is deliberately left
    # unresolved rather than guessed at - matching this codebase's ADR 0007
    # deterministic, no-guessing precedent (see Kafka topic resolution).
    function_node_id_by_bare_name: dict[str, str | None] = {}
    pending_calls: list[tuple[str, list[str]]] = []

    def register_function(
        function: PythonFunction, qualified_name: str, class_name: str | None
    ) -> str:
        node_id = _function_node_id(repository_id, qualified_name)
        properties: dict[str, object] = {
            "name": function.name,
            "file_path": function.location.file_path,
            "decorators": list(function.decorators),
        }
        if class_name is not None:
            properties["class_name"] = class_name
        nodes.append(GraphNode(id=node_id, labels=["Component", "Function"], properties=properties))
        if function.name in function_node_id_by_bare_name:
            function_node_id_by_bare_name[function.name] = None
        else:
            function_node_id_by_bare_name[function.name] = node_id
        pending_calls.append((node_id, function.calls))
        return node_id

    for module in model.python_modules:
        module_id = module_node_id_by_name[module.name]
        nodes.append(
            GraphNode(
                id=module_id,
                labels=["Component", "Module"],
                properties={
                    "name": module.name,
                    "package": module.package,
                    "file_path": module.location.file_path,
                },
            )
        )
        edges.append(GraphEdge(source_id=repo_id, target_id=module_id, type="CONTAINS"))

        for imp in module.imports:
            target_module_id = module_node_id_by_name.get(imp.module)
            if target_module_id is not None and target_module_id != module_id:
                edges.append(
                    GraphEdge(source_id=module_id, target_id=target_module_id, type="IMPORTS")
                )

        for function in module.functions:
            function_id = register_function(function, f"{module.name}.{function.name}", None)
            edges.append(GraphEdge(source_id=module_id, target_id=function_id, type="CONTAINS"))

        for python_class in module.classes:
            class_id = _class_node_id(repository_id, module.name, python_class.name)
            nodes.append(
                GraphNode(
                    id=class_id,
                    labels=["Component", "Class"],
                    properties={
                        "name": python_class.name,
                        "package": module.package,
                        "file_path": python_class.location.file_path,
                        "bases": list(python_class.bases),
                        "decorators": list(python_class.decorators),
                    },
                )
            )
            edges.append(GraphEdge(source_id=module_id, target_id=class_id, type="CONTAINS"))

            for base_name in python_class.bases:
                # Resolved only against classes discovered in this same
                # repository, by simple name - cross-module inheritance is
                # common in Python, and fully-qualifying the base would
                # require import resolution (out of scope, see above).
                for other_module in model.python_modules:
                    for candidate in other_module.classes:
                        if candidate.name == base_name:
                            base_id = _class_node_id(
                                repository_id, other_module.name, candidate.name
                            )
                            edges.append(
                                GraphEdge(
                                    source_id=class_id, target_id=base_id, type="INHERITS_FROM"
                                )
                            )

            for method in python_class.methods:
                method_id = register_function(
                    method, f"{module.name}.{python_class.name}.{method.name}", python_class.name
                )
                edges.append(GraphEdge(source_id=class_id, target_id=method_id, type="CONTAINS"))

    for source_id, calls in pending_calls:
        for raw_call in calls:
            bare_name = raw_call.rsplit(".", 1)[-1]
            target_id = function_node_id_by_bare_name.get(bare_name)
            if target_id is not None and target_id != source_id:
                edges.append(GraphEdge(source_id=source_id, target_id=target_id, type="CALLS"))


def build_graph(repository_id: str, model: ArchitectureModel) -> GraphPayload:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    repo_id = _repository_node_id(repository_id)
    nodes.append(
        GraphNode(
            id=repo_id,
            labels=["Repository"],
            properties={"language": model.language, "framework": model.framework or ""},
        )
    )

    # Maps a bare class name (as recorded on Kafka producer/consumer usages,
    # which don't carry package info) to the node id of the Controller/
    # Service/FeignClient it belongs to, if that class was itself discovered.
    component_by_class_name: dict[str, str] = {}

    for controller in model.controllers:
        node_id = _controller_node_id(repository_id, controller.package, controller.name)
        component_by_class_name[controller.name] = node_id
        nodes.append(
            GraphNode(
                id=node_id,
                labels=["Component", "Controller"],
                properties={
                    "name": controller.name,
                    "package": controller.package,
                    "base_path": controller.base_path,
                    "file_path": controller.location.file_path,
                },
            )
        )
        edges.append(GraphEdge(source_id=repo_id, target_id=node_id, type="CONTAINS"))

        for endpoint in controller.endpoints:
            endpoint_id = _endpoint_node_id(
                node_id, endpoint.http_method, endpoint.path, endpoint.handler_method
            )
            nodes.append(
                GraphNode(
                    id=endpoint_id,
                    labels=["Endpoint"],
                    properties={
                        "http_method": endpoint.http_method,
                        "path": endpoint.path,
                        "handler_method": endpoint.handler_method,
                        "file_path": endpoint.location.file_path,
                    },
                )
            )
            edges.append(GraphEdge(source_id=node_id, target_id=endpoint_id, type="EXPOSES"))

    for service in model.services:
        node_id = _service_node_id(repository_id, service.package, service.name)
        component_by_class_name[service.name] = node_id
        nodes.append(
            GraphNode(
                id=node_id,
                labels=["Component", "Service"],
                properties={
                    "name": service.name,
                    "package": service.package,
                    "file_path": service.location.file_path,
                },
            )
        )
        edges.append(GraphEdge(source_id=repo_id, target_id=node_id, type="CONTAINS"))

    for feign_client in model.feign_clients:
        node_id = _feign_client_node_id(repository_id, feign_client.package, feign_client.name)
        component_by_class_name[feign_client.name] = node_id
        nodes.append(
            GraphNode(
                id=node_id,
                labels=["Component", "FeignClient"],
                properties={
                    "name": feign_client.name,
                    "package": feign_client.package,
                    "target_name": feign_client.target_name,
                    "target_url": feign_client.target_url or "",
                    "file_path": feign_client.location.file_path,
                },
            )
        )
        edges.append(GraphEdge(source_id=repo_id, target_id=node_id, type="CONTAINS"))

        for method in feign_client.methods:
            endpoint_id = _endpoint_node_id(
                node_id, method.http_method, method.path, method.method_name
            )
            nodes.append(
                GraphNode(
                    id=endpoint_id,
                    labels=["Endpoint"],
                    properties={
                        "http_method": method.http_method,
                        "path": method.path,
                        "handler_method": method.method_name,
                        "file_path": feign_client.location.file_path,
                    },
                )
            )
            edges.append(GraphEdge(source_id=node_id, target_id=endpoint_id, type="CALLS"))

    def _owning_component_id(class_name: str, file_path: str) -> str:
        """The node id for whichever Controller/Service/FeignClient this
        class is, or a bare Component node if it's some other class (e.g.
        a plain Kafka helper with no Spring stereotype annotation)."""
        if class_name in component_by_class_name:
            return component_by_class_name[class_name]

        node_id = _generic_component_node_id(repository_id, class_name)
        if not any(node.id == node_id for node in nodes):
            nodes.append(
                GraphNode(
                    id=node_id,
                    labels=["Component"],
                    properties={"name": class_name, "file_path": file_path},
                )
            )
            edges.append(GraphEdge(source_id=repo_id, target_id=node_id, type="CONTAINS"))
        return node_id

    for producer in model.kafka_producers:
        owner_id = _owning_component_id(producer.class_name, producer.location.file_path)
        topic_id = _kafka_topic_node_id(repository_id, producer.topic)
        nodes.append(
            GraphNode(id=topic_id, labels=["KafkaTopic"], properties={"name": producer.topic})
        )
        edges.append(
            GraphEdge(
                source_id=owner_id,
                target_id=topic_id,
                type="PRODUCES_TO",
                properties={"method_name": producer.method_name},
            )
        )

    for consumer in model.kafka_consumers:
        owner_id = _owning_component_id(consumer.class_name, consumer.location.file_path)
        topic_id = _kafka_topic_node_id(repository_id, consumer.topic)
        nodes.append(
            GraphNode(id=topic_id, labels=["KafkaTopic"], properties={"name": consumer.topic})
        )
        edges.append(
            GraphEdge(
                source_id=owner_id,
                target_id=topic_id,
                type="CONSUMES_FROM",
                properties={
                    "method_name": consumer.method_name,
                    "group_id": consumer.group_id or "",
                },
            )
        )

    for dependency in model.maven_dependencies:
        node_id = _dependency_node_id(repository_id, dependency.group_id, dependency.artifact_id)
        nodes.append(
            GraphNode(
                id=node_id,
                labels=["MavenDependency"],
                properties={
                    "group_id": dependency.group_id,
                    "artifact_id": dependency.artifact_id,
                    "version": dependency.version or "",
                    "scope": dependency.scope or "",
                },
            )
        )
        edges.append(GraphEdge(source_id=repo_id, target_id=node_id, type="DEPENDS_ON"))

    _build_python_graph(repository_id, repo_id, model, nodes, edges)

    for python_dependency in model.python_dependencies:
        node_id = _python_dependency_node_id(repository_id, python_dependency.name)
        nodes.append(
            GraphNode(
                id=node_id,
                labels=["PythonDependency"],
                properties={
                    "name": python_dependency.name,
                    "version": python_dependency.version or "",
                },
            )
        )
        edges.append(GraphEdge(source_id=repo_id, target_id=node_id, type="DEPENDS_ON"))

    # A KafkaTopic node is appended once per producer/consumer usage of it,
    # so the same id can appear several times with identical properties -
    # harmless for Neo4j's MERGE, but no reason to send duplicates.
    deduped_nodes = list({node.id: node for node in nodes}.values())

    return GraphPayload(nodes=deduped_nodes, edges=edges)
