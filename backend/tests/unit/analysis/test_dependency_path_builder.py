from app.analysis.graph.models import TraversalHop
from app.analysis.services.dependency_path_builder import build_dependency_paths
from app.graph.models import GraphNode

PRODUCER = GraphNode(
    id="r1:component:Producer", labels=["GraphNode", "Component"], properties={"name": "Producer"}
)
CONTROLLER = GraphNode(
    id="r1:controller:X",
    labels=["GraphNode", "Component", "Controller"],
    properties={"name": "XController"},
)
ENDPOINT = GraphNode(
    id="r1:endpoint:GET",
    labels=["GraphNode", "Endpoint"],
    properties={"http_method": "GET", "path": "/x"},
)
TOPIC = GraphNode(
    id="r1:kafka-topic:orders",
    labels=["GraphNode", "KafkaTopic"],
    properties={"name": "orders", "repository_id": "r1"},
)
SAME_REPO_PEER = GraphNode(
    id="r1:component:Consumer", labels=["GraphNode", "Component"], properties={"name": "Consumer"}
)
CROSS_REPO_TOPIC = GraphNode(
    id="r2:kafka-topic:orders",
    labels=["GraphNode", "KafkaTopic"],
    properties={"name": "orders", "repository_id": "r2"},
)
CROSS_REPO_PEER = GraphNode(
    id="r2:component:OtherConsumer",
    labels=["GraphNode", "Component"],
    properties={"name": "OtherConsumer"},
)


def test_api_hop_becomes_a_two_step_path() -> None:
    hop = TraversalHop(from_node=CONTROLLER, relationship="EXPOSES", to_node=ENDPOINT)

    paths = build_dependency_paths([hop], [], [], [])

    assert len(paths) == 1
    step_ids = [step.node_id for step in paths[0].steps]
    assert step_ids == [CONTROLLER.id, ENDPOINT.id]
    assert paths[0].steps[0].relationship is None
    assert paths[0].steps[1].relationship == "EXPOSES"


def test_topic_hop_with_no_peers_stays_a_two_step_path() -> None:
    hop = TraversalHop(from_node=PRODUCER, relationship="PRODUCES_TO", to_node=TOPIC)

    paths = build_dependency_paths([], [hop], [], [])

    assert len(paths) == 1
    assert [step.node_id for step in paths[0].steps] == [PRODUCER.id, TOPIC.id]


def test_topic_hop_with_same_repository_peer_becomes_a_three_step_path() -> None:
    topic_hop = TraversalHop(from_node=PRODUCER, relationship="PRODUCES_TO", to_node=TOPIC)
    peer_hop = TraversalHop(from_node=SAME_REPO_PEER, relationship="CONSUMES_FROM", to_node=TOPIC)

    paths = build_dependency_paths([], [topic_hop], [peer_hop], [])

    assert len(paths) == 1
    steps = paths[0].steps
    assert [step.node_id for step in steps] == [PRODUCER.id, TOPIC.id, SAME_REPO_PEER.id]
    assert steps[1].relationship == "PRODUCES_TO"
    assert steps[2].relationship == "CONSUMES_FROM"


def test_topic_hop_with_cross_repository_peer_matches_by_topic_name() -> None:
    topic_hop = TraversalHop(from_node=PRODUCER, relationship="PRODUCES_TO", to_node=TOPIC)
    cross_peer_hop = TraversalHop(
        from_node=CROSS_REPO_PEER, relationship="CONSUMES_FROM", to_node=CROSS_REPO_TOPIC
    )

    paths = build_dependency_paths([], [topic_hop], [], [cross_peer_hop])

    assert len(paths) == 1
    steps = paths[0].steps
    assert [step.node_id for step in steps] == [PRODUCER.id, TOPIC.id, CROSS_REPO_PEER.id]


def test_topic_hop_with_both_same_and_cross_repository_peers_emits_one_path_per_peer() -> None:
    topic_hop = TraversalHop(from_node=PRODUCER, relationship="PRODUCES_TO", to_node=TOPIC)
    same_peer_hop = TraversalHop(
        from_node=SAME_REPO_PEER, relationship="CONSUMES_FROM", to_node=TOPIC
    )
    cross_peer_hop = TraversalHop(
        from_node=CROSS_REPO_PEER, relationship="CONSUMES_FROM", to_node=CROSS_REPO_TOPIC
    )

    paths = build_dependency_paths([], [topic_hop], [same_peer_hop], [cross_peer_hop])

    assert len(paths) == 2
    terminal_ids = {path.steps[-1].node_id for path in paths}
    assert terminal_ids == {SAME_REPO_PEER.id, CROSS_REPO_PEER.id}


def test_mixed_hops_with_and_without_peers() -> None:
    other_topic = GraphNode(
        id="r1:kafka-topic:payments",
        labels=["GraphNode", "KafkaTopic"],
        properties={"name": "payments", "repository_id": "r1"},
    )
    topic_hop_with_peer = TraversalHop(
        from_node=PRODUCER, relationship="PRODUCES_TO", to_node=TOPIC
    )
    topic_hop_without_peer = TraversalHop(
        from_node=PRODUCER, relationship="PRODUCES_TO", to_node=other_topic
    )
    peer_hop = TraversalHop(from_node=SAME_REPO_PEER, relationship="CONSUMES_FROM", to_node=TOPIC)

    paths = build_dependency_paths(
        [], [topic_hop_with_peer, topic_hop_without_peer], [peer_hop], []
    )

    assert len(paths) == 2
    lengths = sorted(len(path.steps) for path in paths)
    assert lengths == [2, 3]
