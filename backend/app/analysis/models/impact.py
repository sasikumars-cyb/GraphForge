"""The impact-analysis output shape.

Field names here are chosen to match the Pydantic response schema
(`app.schemas.analysis`) 1:1, so persisting a result to the `JSON` columns
on `PullRequestAnalysis` (via `dataclasses.asdict`) needs no field-name
translation.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.graph.models import GraphNode


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class ImpactedNode:
    """One node the analysis considers impacted — a service, an API
    endpoint, a Kafka topic, or a Maven dependency."""

    id: str
    name: str
    node_type: str
    repository_id: str


@dataclass(frozen=True)
class DependencyPathStep:
    """One hop in a `DependencyPath`. `relationship` describes how this
    step connects to the *previous* step in the chain (`None` for the
    first step) — it names the graph relationship type involved, not
    necessarily a single edge pointing in the same direction as the
    reading order (e.g. a same-topic peer is reached by walking a
    `CONSUMES_FROM` edge *from* the peer *to* the topic, not the other way
    around; the step still reads left-to-right as "topic -> peer via
    CONSUMES_FROM")."""

    node_id: str
    node_name: str
    node_type: str
    relationship: str | None = None


@dataclass(frozen=True)
class DependencyPath:
    steps: list[DependencyPathStep] = field(default_factory=list)


@dataclass(frozen=True)
class ImpactAnalysisResult:
    risk: RiskLevel
    directly_impacted_services: list[ImpactedNode] = field(default_factory=list)
    indirectly_impacted_services: list[ImpactedNode] = field(default_factory=list)
    impacted_apis: list[ImpactedNode] = field(default_factory=list)
    impacted_topics: list[ImpactedNode] = field(default_factory=list)
    impacted_libraries: list[ImpactedNode] = field(default_factory=list)
    dependency_paths: list[DependencyPath] = field(default_factory=list)


# Preference order for a node's "primary" (most specific) label, used for
# both `ImpactedNode.node_type` and path-step display. Every node also
# carries the generic `GraphNode` base label - never chosen as primary.
_LABEL_PRIORITY = (
    "Controller",
    "Service",
    "FeignClient",
    "KafkaTopic",
    "MavenDependency",
    "Endpoint",
    "Component",
    "Repository",
)


def primary_label(node: GraphNode) -> str:
    for label in _LABEL_PRIORITY:
        if label in node.labels:
            return label
    return next((label for label in node.labels if label != "GraphNode"), "GraphNode")


def display_name(node: GraphNode) -> str:
    """A human-readable label for a node - not every node type carries a
    `name` property (Endpoints and Maven dependencies don't)."""
    if "Endpoint" in node.labels:
        method = node.properties.get("http_method", "?")
        path = node.properties.get("path", "?")
        return f"{method} {path}"
    if "MavenDependency" in node.labels:
        group_id = node.properties.get("group_id", "?")
        artifact_id = node.properties.get("artifact_id", "?")
        return f"{group_id}:{artifact_id}"
    name = node.properties.get("name")
    return str(name) if name else node.id


def impacted_node_from_graph_node(
    node: GraphNode, repository_id: str | None = None
) -> ImpactedNode:
    """`repository_id` defaults to the node's own `repository_id` property
    - only pass an override for nodes that legitimately belong to a
    *different* repository than the one being analyzed (cross-repository
    downstream peers)."""
    return ImpactedNode(
        id=node.id,
        name=display_name(node),
        node_type=primary_label(node),
        repository_id=repository_id or str(node.properties.get("repository_id", "")),
    )
