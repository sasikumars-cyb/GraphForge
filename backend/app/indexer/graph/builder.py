"""Turns an `ArchitectureModel` into the generic `GraphPayload`
`app.graph` persists to Neo4j — the one place that knows how a discovered
Java entity maps to a graph label and relationship type.

Node id scheme: every id is namespaced `f"{repository_id}:{kind}:{key}"`,
so re-indexing the same repository always produces the same ids (MERGE
upserts in place) and ids never collide across repositories.
"""

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.indexer.models.architecture import ArchitectureModel


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

    # A KafkaTopic node is appended once per producer/consumer usage of it,
    # so the same id can appear several times with identical properties -
    # harmless for Neo4j's MERGE, but no reason to send duplicates.
    deduped_nodes = list({node.id: node for node in nodes}.values())

    return GraphPayload(nodes=deduped_nodes, edges=edges)
