"""`_strip_neo4j_readback_artifacts` — pure unit test, no I/O. Proves the
Neo4j read-back normalization the Graph Validation Dashboard's integration
test discovered was necessary (the comparator itself stays untouched;
this is where the fix belongs)."""

from __future__ import annotations

from app.graph.models import GraphNode, GraphPayload
from app.services.parity_service import _strip_neo4j_readback_artifacts


def test_strips_synthetic_graphnode_label_and_readback_properties() -> None:
    payload = GraphPayload(
        nodes=[
            GraphNode(
                id="repo:component:Foo",
                labels=["GraphNode", "Component"],
                properties={
                    "id": "repo:component:Foo",
                    "repository_id": "repo",
                    "name": "Foo",
                },
            )
        ]
    )

    normalized = _strip_neo4j_readback_artifacts(payload)

    node = normalized.nodes[0]
    assert node.labels == ["Component"]
    assert node.properties == {"name": "Foo"}
    assert node.id == "repo:component:Foo"  # the real node id field is untouched


def test_leaves_a_node_with_no_artifacts_unchanged() -> None:
    payload = GraphPayload(
        nodes=[GraphNode(id="a", labels=["Component"], properties={"name": "A"})]
    )

    normalized = _strip_neo4j_readback_artifacts(payload)

    assert normalized.nodes[0].labels == ["Component"]
    assert normalized.nodes[0].properties == {"name": "A"}


def test_edges_pass_through_unmodified() -> None:
    from app.graph.models import GraphEdge

    edge = GraphEdge(source_id="a", target_id="b", type="CALLS", properties={"x": 1})
    payload = GraphPayload(nodes=[], edges=[edge])

    normalized = _strip_neo4j_readback_artifacts(payload)

    assert normalized.edges == [edge]
