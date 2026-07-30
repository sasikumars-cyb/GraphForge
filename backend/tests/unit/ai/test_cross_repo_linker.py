"""Unit tests for the cross-repository link rules — pure functions over
`RepoNodes`, no Neo4j or DB involved. Real end-to-end writing/reading of
the resulting edges is covered by
`tests/integration/test_cross_repo_linker.py`.
"""

from __future__ import annotations

from app.graph.models import GraphNode
from app.indexer.graph.cross_repo_linker import (
    CROSS_REPO_LINK_RULES,
    RepoNodes,
    _identifier_match,
    _kafka_topic_overlap,
    _shared_dependency_name,
)
from app.indexer.graph.cross_repo_linker import (
    _feign_service_calls as feign_service_calls,
)


def _repo(
    repository_id: str,
    name: str,
    *,
    feign_clients: list[GraphNode] | None = None,
    maven_dependencies: list[GraphNode] | None = None,
    python_dependencies: list[GraphNode] | None = None,
    produces: frozenset[str] = frozenset(),
    consumes: frozenset[str] = frozenset(),
) -> RepoNodes:
    return RepoNodes(
        repository_id=repository_id,
        name=name,
        feign_clients=feign_clients or [],
        maven_dependencies=maven_dependencies or [],
        python_dependencies=python_dependencies or [],
        produces_topic_names=produces,
        consumes_topic_names=consumes,
    )


def test_identifier_match_is_exact_or_suffix_normalized_never_substring() -> None:
    assert _identifier_match("etl-core", "etl-core")
    assert _identifier_match("etl-core-service", "etl-core")
    assert _identifier_match("ETL-CORE-CLIENT", "etl-core")
    # Never a substring match — "etl-core-utils" must not match "etl-core".
    assert not _identifier_match("etl-core-utils", "etl-core")
    assert not _identifier_match("", "etl-core")
    assert not _identifier_match("etl-core", "")


def test_feign_service_calls_matches_target_name_to_other_repo_name() -> None:
    feign_node = GraphNode(
        id="repo-a:feign:x.EtlCoreClient",
        labels=["Component", "FeignClient"],
        properties={"name": "EtlCoreClient", "target_name": "etl-core-service"},
    )
    source = _repo("repo-a", "ingestion-framework", feign_clients=[feign_node])
    other = _repo("repo-b", "etl-core")

    edges = feign_service_calls(source, other)

    assert len(edges) == 1
    edge = edges[0]
    assert edge.source_id == "repo-a:repository"
    assert edge.target_id == "repo-b:repository"
    assert edge.type == "CALLS_SERVICE"
    assert edge.properties["target_name"] == ["etl-core-service"]
    assert edge.properties["via"] == ["EtlCoreClient"]
    # ADR 0010 (Theme E) — a literal Feign target is a structural match.
    assert edge.properties["confidence"] == "structural"


def test_feign_service_calls_no_match_produces_no_edge() -> None:
    feign_node = GraphNode(
        id="repo-a:feign:x.UnrelatedClient",
        labels=["Component", "FeignClient"],
        properties={"name": "UnrelatedClient", "target_name": "some-other-service"},
    )
    source = _repo("repo-a", "ingestion-framework", feign_clients=[feign_node])
    other = _repo("repo-b", "etl-core")

    assert feign_service_calls(source, other) == []


def test_kafka_topic_overlap_detects_shared_topic_regardless_of_direction() -> None:
    source = _repo("repo-a", "ingestion-framework", produces={"orders-created"})
    other = _repo("repo-b", "etl-core", consumes={"orders-created"})

    edges = _kafka_topic_overlap(source, other)

    assert len(edges) == 1
    assert edges[0].type == "SHARES_TOPIC"
    assert edges[0].properties["topics"] == ["orders-created"]
    assert edges[0].properties["confidence"] == "structural"


def test_kafka_topic_overlap_aggregates_multiple_topics_into_one_edge() -> None:
    source = _repo("repo-a", "ingestion-framework", produces={"orders-created", "orders-updated"})
    other = _repo("repo-b", "etl-core", consumes={"orders-created", "orders-updated"})

    edges = _kafka_topic_overlap(source, other)

    # One edge per repo pair, not one per topic — a second MERGE on the same
    # (source, target, type) would silently overwrite the first topic's
    # properties instead of accumulating them.
    assert len(edges) == 1
    assert edges[0].properties["topics"] == ["orders-created", "orders-updated"]


def test_kafka_topic_overlap_no_shared_topic_produces_no_edge() -> None:
    source = _repo("repo-a", "ingestion-framework", produces={"orders-created"})
    other = _repo("repo-b", "etl-core", consumes={"unrelated-topic"})

    assert _kafka_topic_overlap(source, other) == []


def test_shared_dependency_name_matches_maven_artifact_to_other_repo_name() -> None:
    dep_node = GraphNode(
        id="repo-a:dependency:com.acme:etl-core",
        labels=["MavenDependency"],
        properties={"group_id": "com.acme", "artifact_id": "etl-core"},
    )
    source = _repo("repo-a", "ingestion-framework", maven_dependencies=[dep_node])
    other = _repo("repo-b", "etl-core")

    edges = _shared_dependency_name(source, other)

    assert len(edges) == 1
    assert edges[0].type == "DEPENDS_ON_REPOSITORY"
    assert edges[0].properties["confidence"] == "heuristic"
    assert edges[0].properties["dependencies"] == ["etl-core"]


def test_shared_dependency_name_matches_python_dependency_to_other_repo_name() -> None:
    dep_node = GraphNode(
        id="repo-a:python-dependency:etl-core",
        labels=["PythonDependency"],
        properties={"name": "etl-core"},
    )
    source = _repo("repo-a", "ingestion-framework", python_dependencies=[dep_node])
    other = _repo("repo-b", "etl-core")

    edges = _shared_dependency_name(source, other)

    assert len(edges) == 1
    assert edges[0].properties["dependencies"] == ["etl-core"]


def test_shared_dependency_name_no_match_produces_no_edge() -> None:
    dep_node = GraphNode(
        id="repo-a:dependency:org.apache:commons-lang3",
        labels=["MavenDependency"],
        properties={"group_id": "org.apache", "artifact_id": "commons-lang3"},
    )
    source = _repo("repo-a", "ingestion-framework", maven_dependencies=[dep_node])
    other = _repo("repo-b", "etl-core")

    assert _shared_dependency_name(source, other) == []


def test_registry_has_exactly_the_three_documented_rules() -> None:
    names = {rule.name for rule in CROSS_REPO_LINK_RULES}
    assert names == {"feign_service_calls", "kafka_topic_overlap", "shared_dependency_name"}
    rel_types = {rule.rel_type for rule in CROSS_REPO_LINK_RULES}
    assert rel_types == {"CALLS_SERVICE", "SHARES_TOPIC", "DEPENDS_ON_REPOSITORY"}
