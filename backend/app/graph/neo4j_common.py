"""Shared helpers for turning a neo4j driver `Node` value into a
`GraphNode` - used by both `Neo4jGraphRepository` and
`app.analysis.graph.neo4j_impact_reader.Neo4jImpactGraphReader`, which
read from the same Neo4j database but for different purposes.

Also the one shared label/relationship-type allowlist for every Neo4j
writer in this codebase (`Neo4jGraphRepository` for the code graph,
`Neo4jTestCaseGraphRepository` for TestRail data) - Cypher can't
parameterize label names or relationship types, so both are interpolated
directly into query strings, safe only because they exclusively come from
`_ALLOWED_LABELS`/`_ALLOWED_REL_TYPES` below, never from request input.
"""

from typing import Any

from app.graph.models import GraphNode

# Every node/relationship type any writer in this codebase ever writes.
# Extend when a writer's vocabulary grows (see app.indexer.graph.builder,
# app.indexer.graph.testrail_builder) - anything not listed here is
# refused, not silently interpolated.
_ALLOWED_LABELS = frozenset(
    {
        "GraphNode",
        "Repository",
        "Component",
        "Controller",
        "Service",
        "FeignClient",
        "Endpoint",
        "KafkaTopic",
        "MavenDependency",
        "Module",
        "Class",
        "Function",
        "PythonDependency",
        "DataTable",
        "SqlFile",
        "SourceFile",
        "GenericSymbol",
        "TestRailProject",
        "TestSuite",
        "TestSection",
        "TestCase",
    }
)
_ALLOWED_REL_TYPES = frozenset(
    {
        "CONTAINS",
        "EXPOSES",
        "CALLS",
        "PRODUCES_TO",
        "CONSUMES_FROM",
        "DEPENDS_ON",
        "IMPORTS",
        "INHERITS_FROM",
        "READS_FROM",
        "WRITES_TO",
        "LOADS_SQL",
        # Cross-repository relationships — see
        # app.indexer.graph.cross_repo_linker, the only writer of these.
        # Unlike every other relationship above, both endpoints of these
        # three carry *different* `repository_id` values (each repository's
        # own `Repository` node) - the one deliberate exception to this
        # module's per-repository isolation.
        "CALLS_SERVICE",
        "SHARES_TOPIC",
        "DEPENDS_ON_REPOSITORY",
    }
)

# Base label every node gets, regardless of its semantic labels - lets a
# single index cover `id` lookups for every node type.
_BASE_LABEL = "GraphNode"


def validate_labels(labels: list[str]) -> tuple[str, ...]:
    unknown = set(labels) - _ALLOWED_LABELS
    if unknown:
        raise ValueError(f"Refusing to write unknown graph label(s): {sorted(unknown)}")
    ordered = [_BASE_LABEL, *sorted(label for label in labels if label != _BASE_LABEL)]
    # dict.fromkeys: de-dupe while preserving order (a node may already list _BASE_LABEL).
    return tuple(dict.fromkeys(ordered))


def validate_rel_type(rel_type: str) -> str:
    if rel_type not in _ALLOWED_REL_TYPES:
        raise ValueError(f"Refusing to write unknown relationship type: {rel_type!r}")
    return rel_type


def label_cypher(labels: tuple[str, ...]) -> str:
    return "".join(f":`{label}`" for label in labels)


def node_from_value(value: Any) -> GraphNode:
    return GraphNode(id=value["id"], labels=list(value.labels), properties=dict(value))
