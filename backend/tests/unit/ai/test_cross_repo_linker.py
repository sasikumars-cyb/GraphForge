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
    _downgrade_ambiguous_imports,
    _identifier_match,
    _kafka_topic_overlap,
    _shared_dependency_name,
    _source_level_import,
    compute_edges,
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
    python_imports: list[GraphNode] | None = None,
    package_name: str | None = None,
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
        python_imports=python_imports or [],
        package_name=package_name,
    )


def _import_node(repository_id: str, module: str, imported_names: list[str] | None = None) -> GraphNode:
    return GraphNode(
        id=f"{repository_id}:python-import:{module}",
        labels=["PythonImport"],
        properties={"module": module, "imported_names": imported_names or [], "file_paths": []},
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


def test_registry_has_exactly_the_four_documented_rules() -> None:
    # RFC-0012 added a fourth rule, "source_level_import" — see
    # `cross_repo_linker._source_level_import`.
    names = {rule.name for rule in CROSS_REPO_LINK_RULES}
    assert names == {
        "feign_service_calls",
        "kafka_topic_overlap",
        "shared_dependency_name",
        "source_level_import",
    }
    rel_types = {rule.rel_type for rule in CROSS_REPO_LINK_RULES}
    assert rel_types == {
        "CALLS_SERVICE",
        "SHARES_TOPIC",
        "DEPENDS_ON_REPOSITORY",
        "IMPORTS_REPOSITORY",
    }


# ---------------------------------------------------------------------------
# RFC-0012 — source-level import evidence (`_source_level_import`).
# ---------------------------------------------------------------------------


def test_source_level_import_matches_import_to_other_repo_name() -> None:
    """Requirement: `import X` (or `from X import Y`) is captured as
    evidence — matched here against the other repository's git name."""
    source = _repo("repo-a", "ingestion-framework", python_imports=[_import_node("repo-a", "etl-core")])
    other = _repo("repo-b", "etl-core")

    edges = _source_level_import(source, other)

    assert len(edges) == 1
    assert edges[0].type == "IMPORTS_REPOSITORY"
    assert edges[0].properties["confidence"] == "heuristic"
    assert edges[0].properties["imports"] == ["etl-core"]


def test_source_level_import_matches_import_to_other_repos_own_package_name() -> None:
    """The real-world case RFC-0012 exists for: a repository's git name
    (`up-databricks-shared-jobs`) and its self-declared package name
    (`shared_jobs`) commonly differ — an import names the *package*, so
    matching must also check `RepoNodes.package_name`, not just `name`."""
    source = _repo("repo-a", "caller-repo", python_imports=[_import_node("repo-a", "shared_jobs")])
    other = _repo("repo-b", "up-databricks-shared-jobs", package_name="shared_jobs")

    edges = _source_level_import(source, other)

    assert len(edges) == 1
    assert edges[0].properties["imports"] == ["shared_jobs"]


def test_source_level_import_no_match_produces_no_edge() -> None:
    """Requirement: an import matching no indexed repository must not
    fabricate a relationship — the underlying `PythonImport` node/evidence
    still exists in the graph regardless (written unconditionally by
    `graph/builder.py`); only the *edge* is absent here."""
    source = _repo("repo-a", "ingestion-framework", python_imports=[_import_node("repo-a", "numpy")])
    other = _repo("repo-b", "etl-core", package_name="etl_core_pkg")

    assert _source_level_import(source, other) == []


def test_source_level_import_never_a_substring_match() -> None:
    source = _repo(
        "repo-a", "ingestion-framework", python_imports=[_import_node("repo-a", "etl-core-utils")]
    )
    other = _repo("repo-b", "etl-core")

    assert _source_level_import(source, other) == []


# ---------------------------------------------------------------------------
# RFC-0012 — ambiguous imports must not fabricate a strong relationship.
# ---------------------------------------------------------------------------


def test_ambiguous_import_across_multiple_repositories_is_downgraded_not_dropped() -> None:
    """Requirement: an import matching multiple repositories is retained
    but must not read as unambiguous evidence — `compute_edges`'s global
    view (across every `other`, unlike a single pairwise rule call) is
    what makes this detectable at all."""
    imp = _import_node("repo-a", "widgets")
    source = _repo("repo-a", "caller-repo", python_imports=[imp])
    other_x = _repo("repo-b", "widgets-x", package_name="widgets")
    other_y = _repo("repo-c", "widgets-y", package_name="widgets")

    edges_by_repo = compute_edges({"repo-a": source, "repo-b": other_x, "repo-c": other_y})

    import_edges = [e for e in edges_by_repo["repo-a"] if e.type == "IMPORTS_REPOSITORY"]
    assert len(import_edges) == 2, "both matches are retained, not dropped"
    assert {e.properties["confidence"] for e in import_edges} == {"ambiguous"}


def test_unambiguous_import_across_multiple_repositories_stays_heuristic() -> None:
    """A source with imports matching two *different, non-overlapping*
    modules — each unambiguous on its own — must not have either
    downgraded just because the source has more than one import edge."""
    source = _repo(
        "repo-a",
        "caller-repo",
        python_imports=[_import_node("repo-a", "widgets"), _import_node("repo-a", "gadgets")],
    )
    other_widgets = _repo("repo-b", "widgets")
    other_gadgets = _repo("repo-c", "gadgets")

    edges_by_repo = compute_edges(
        {"repo-a": source, "repo-b": other_widgets, "repo-c": other_gadgets}
    )

    import_edges = [e for e in edges_by_repo["repo-a"] if e.type == "IMPORTS_REPOSITORY"]
    assert len(import_edges) == 2
    assert all(e.properties["confidence"] == "heuristic" for e in import_edges)


def test_downgrade_ambiguous_imports_leaves_other_rel_types_untouched() -> None:
    """Existing Feign/Kafka/manifest-dependency edges must be byte-for-byte
    unaffected by the new ambiguity pass — requirements #7/#8."""
    feign_edge = feign_service_calls(
        _repo(
            "repo-a",
            "ingestion-framework",
            feign_clients=[
                GraphNode(
                    id="repo-a:feign:x.EtlCoreClient",
                    labels=["Component", "FeignClient"],
                    properties={"name": "EtlCoreClient", "target_name": "etl-core"},
                )
            ],
        ),
        _repo("repo-b", "etl-core"),
    )
    result = _downgrade_ambiguous_imports(feign_edge)
    assert result == feign_edge


# ---------------------------------------------------------------------------
# RFC-0012 — the PROT-5764 shape, using the actual names, as the
# integration check that the mechanism resolves the real case it was
# built for (see this file's module docstring precedent and
# `test_repository_candidate_verification.py`'s equivalent). The
# *mechanism* under test (`_source_level_import`/`compute_edges`) contains
# no reference to either name.
# ---------------------------------------------------------------------------


def test_prot_5764_source_import_produces_the_expected_relationship() -> None:
    avangrid = _repo(
        "repo-avangrid",
        "ds-databricks-avangrid-em-ct-dataingest",
        python_imports=[_import_node("repo-avangrid", "shared_jobs", ["GatherTrackTraceErrorsSharedJob"])],
    )
    shared_jobs = _repo(
        "repo-shared-jobs", "up-databricks-shared-jobs", package_name="shared_jobs"
    )

    edges_by_repo = compute_edges({"repo-avangrid": avangrid, "repo-shared-jobs": shared_jobs})

    import_edges = [e for e in edges_by_repo["repo-avangrid"] if e.type == "IMPORTS_REPOSITORY"]
    assert len(import_edges) == 1
    edge = import_edges[0]
    assert edge.target_id == "repo-shared-jobs:repository"
    assert edge.properties["confidence"] == "heuristic"
    assert edge.properties["imports"] == ["shared_jobs"]
