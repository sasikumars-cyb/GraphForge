"""Turns raw `TraversalHop`s into human-readable `DependencyPath`s -
explains *why* something is impacted, not just *that* it is.

A path from a directly-changed node to a Kafka topic is only emitted on
its own when nothing else is connected to that topic (nothing to explain
yet); once a peer (same-repository or cross-repository) shares the topic,
a single 3-step path (`changed node -> topic -> peer`) replaces it -
showing the peer already implies the topic.
"""

from collections import defaultdict

from app.analysis.graph.models import TraversalHop
from app.analysis.models.impact import (
    DependencyPath,
    DependencyPathStep,
    display_name,
    primary_label,
)
from app.graph.models import GraphNode


def _step(node: GraphNode, relationship: str | None) -> DependencyPathStep:
    return DependencyPathStep(
        node_id=node.id,
        node_name=display_name(node),
        node_type=primary_label(node),
        relationship=relationship,
    )


def _two_step_path(hop: TraversalHop) -> DependencyPath:
    return DependencyPath(steps=[_step(hop.from_node, None), _step(hop.to_node, hop.relationship)])


def _three_step_path(topic_hop: TraversalHop, peer_hop: TraversalHop) -> DependencyPath:
    return DependencyPath(
        steps=[
            _step(topic_hop.from_node, None),
            _step(topic_hop.to_node, topic_hop.relationship),
            _step(peer_hop.from_node, peer_hop.relationship),
        ]
    )


def build_dependency_paths(
    api_hops: list[TraversalHop],
    topic_hops: list[TraversalHop],
    same_repository_peer_hops: list[TraversalHop],
    cross_repository_peer_hops: list[TraversalHop],
    service_caller_hops: list[TraversalHop] | None = None,
) -> list[DependencyPath]:
    paths = [_two_step_path(hop) for hop in api_hops]

    # `hop.from_node` is the caller repository, `hop.to_node` is this one -
    # "CallerRepo -[CALLS_SERVICE]-> ThisRepo" reads accurately as-is,
    # unlike the topic-peer hops below (see `_three_step_path`'s docstring
    # for why those need reordering and this doesn't).
    paths.extend(_two_step_path(hop) for hop in service_caller_hops or [])

    peers_by_topic_id: dict[str, list[TraversalHop]] = defaultdict(list)
    for peer_hop in same_repository_peer_hops:
        peers_by_topic_id[peer_hop.to_node.id].append(peer_hop)

    peers_by_topic_name: dict[str, list[TraversalHop]] = defaultdict(list)
    for peer_hop in cross_repository_peer_hops:
        topic_name = peer_hop.to_node.properties.get("name")
        if topic_name:
            peers_by_topic_name[topic_name].append(peer_hop)

    for topic_hop in topic_hops:
        topic_name = topic_hop.to_node.properties.get("name")
        matching_peers = peers_by_topic_id.get(topic_hop.to_node.id, []) + (
            peers_by_topic_name.get(topic_name, []) if topic_name else []
        )
        if not matching_peers:
            paths.append(_two_step_path(topic_hop))
            continue
        paths.extend(_three_step_path(topic_hop, peer_hop) for peer_hop in matching_peers)

    return paths
