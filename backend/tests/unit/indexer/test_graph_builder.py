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
    PythonSqlFileReference,
    SourceLocation,
    SparkTableRead,
    SparkTableWrite,
    SqlFile,
    SqlTableReference,
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


def test_spark_table_read_creates_reads_from_edge_from_owning_module() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(name="jobs.report", package="jobs", location=PY_LOCATION),
        ],
        spark_table_reads=[
            SparkTableRead(
                table_name="bronze.customers", location=PY_LOCATION, function_name="merge_schema"
            )
        ],
    )

    graph = build_graph("repo-1", model)
    table_id = "repo-1:data-table:bronze.customers"
    module_id = "repo-1:module:jobs.report"
    assert any(node.id == table_id and node.labels == ["DataTable"] for node in graph.nodes)
    edge_key = (module_id, table_id, "READS_FROM")
    matching_edges = [e for e in graph.edges if (e.source_id, e.target_id, e.type) == edge_key]
    assert len(matching_edges) == 1
    assert matching_edges[0].properties["function_name"] == "merge_schema"


def test_spark_table_write_creates_writes_to_edge_from_owning_module() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(name="jobs.report", package="jobs", location=PY_LOCATION),
        ],
        spark_table_writes=[
            SparkTableWrite(
                table_name="gold.report",
                method_name="saveAsTable",
                location=PY_LOCATION,
                function_name="write_report",
            )
        ],
    )

    graph = build_graph("repo-1", model)
    table_id = "repo-1:data-table:gold.report"
    module_id = "repo-1:module:jobs.report"
    assert any(node.id == table_id and node.labels == ["DataTable"] for node in graph.nodes)
    edge_key = (module_id, table_id, "WRITES_TO")
    matching_edges = [e for e in graph.edges if (e.source_id, e.target_id, e.type) == edge_key]
    assert len(matching_edges) == 1
    assert matching_edges[0].properties["method_name"] == "saveAsTable"


def test_spark_table_read_with_no_matching_module_is_skipped() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        spark_table_reads=[SparkTableRead(table_name="bronze.customers", location=PY_LOCATION)],
    )

    graph = build_graph("repo-1", model)
    assert not any(e.type == "READS_FROM" for e in graph.edges)


# ---------------------------------------------------------------------------
# is_test / component_type / symbol_type / confidence / language classification
# ---------------------------------------------------------------------------
#
# Regression coverage for the real bug this closes: a Planning run named
# `TestSCDType2Merger` (a pytest test class) as if it were the production
# `SCDType2Merger` it tests. Every Component-labeled node must now carry
# enough classification metadata for a consumer to tell the two apart
# without recomputing a private regex.


def test_python_test_class_is_flagged_is_test_with_high_confidence() -> None:
    test_location = SourceLocation(file_path="tests/unit/test_scd2.py")
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="tests.unit.test_scd2",
                package="tests.unit",
                location=test_location,
                classes=[PythonClass(name="TestSCDType2Merger", location=test_location)],
            )
        ],
    )

    graph = build_graph("repo-1", model)
    class_id = "repo-1:class:tests.unit.test_scd2.TestSCDType2Merger"
    node = next(n for n in graph.nodes if n.id == class_id)

    assert node.properties["is_test"] is True
    assert node.properties["confidence"] == 1.0
    assert node.properties["symbol_type"] == "class"
    assert node.properties["component_type"] == "Class"
    assert node.properties["language"] == "python"


def test_python_production_class_is_not_flagged_is_test() -> None:
    prod_location = SourceLocation(file_path="src/etl_core/scd/scd_type2.py")
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="etl_core.scd.scd_type2",
                package="etl_core.scd",
                location=prod_location,
                classes=[PythonClass(name="SCDType2Merger", location=prod_location)],
            )
        ],
    )

    graph = build_graph("repo-1", model)
    class_id = "repo-1:class:etl_core.scd.scd_type2.SCDType2Merger"
    node = next(n for n in graph.nodes if n.id == class_id)

    assert node.properties["is_test"] is False
    assert node.properties["symbol_type"] == "class"


def test_python_method_symbol_type_distinguishes_from_bare_function() -> None:
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
    method_id = "repo-1:function:app.services.order_service.OrderService.create_order"
    function_id = "repo-1:function:app.services.order_service.build_default_service"
    method_node = next(n for n in graph.nodes if n.id == method_id)
    function_node = next(n for n in graph.nodes if n.id == function_id)

    assert method_node.properties["symbol_type"] == "method"
    assert function_node.properties["symbol_type"] == "function"


def test_java_controller_test_source_root_is_flagged_is_test() -> None:
    test_location = SourceLocation(file_path="src/test/java/com/example/OrderControllerTest.java")
    model = ArchitectureModel(
        language="java",
        framework="spring-boot",
        controllers=[
            Controller(
                name="OrderControllerTest",
                package="com.example",
                base_path="/orders",
                location=test_location,
            )
        ],
    )

    graph = build_graph("repo-1", model)
    controller_id = "repo-1:controller:com.example.OrderControllerTest"
    node = next(n for n in graph.nodes if n.id == controller_id)

    assert node.properties["is_test"] is True
    assert node.properties["component_type"] == "Controller"


def test_java_production_controller_is_not_flagged_is_test() -> None:
    graph = build_graph(
        "repo-1",
        ArchitectureModel(
            language="java",
            framework="spring-boot",
            controllers=[
                Controller(
                    name="OrderController",
                    package="com.example",
                    base_path="/orders",
                    location=LOCATION,
                )
            ],
        ),
    )
    controller_id = "repo-1:controller:com.example.OrderController"
    node = next(n for n in graph.nodes if n.id == controller_id)
    assert node.properties["is_test"] is False


# ---------------------------------------------------------------------------
# spark.sql() / .sql file table lineage (data-flow gap the architecture
# audit found - see docs/architecture-diagrams/06 and the extractors this
# exercises: sql_lineage.py, python/sql_files.py, sql_file_extractor.py).
# ---------------------------------------------------------------------------


def test_spark_read_attributes_to_specific_function_when_unambiguous() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="jobs.report",
                package="jobs",
                location=PY_LOCATION,
                functions=[PythonFunction(name="merge_schema", location=PY_LOCATION)],
            ),
        ],
        spark_table_reads=[
            SparkTableRead(
                table_name="bronze.customers", location=PY_LOCATION, function_name="merge_schema"
            )
        ],
    )

    graph = build_graph("repo-1", model)
    function_id = "repo-1:function:jobs.report.merge_schema"
    table_id = "repo-1:data-table:bronze.customers"
    edge_key = (function_id, table_id, "READS_FROM")
    assert edge_key in {(e.source_id, e.target_id, e.type) for e in graph.edges}
    # Falls back to nothing at the module level for this same edge - it's
    # attributed once, to the function, not duplicated onto the module too.
    module_id = "repo-1:module:jobs.report"
    assert (module_id, table_id, "READS_FROM") not in {
        (e.source_id, e.target_id, e.type) for e in graph.edges
    }


def test_spark_write_falls_back_to_module_when_function_name_is_ambiguous() -> None:
    # "write_report" is a method on two unrelated classes in the same
    # module - matches CALLS resolution's own ambiguity precedent.
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="jobs.report",
                package="jobs",
                location=PY_LOCATION,
                classes=[
                    PythonClass(
                        name="A",
                        location=PY_LOCATION,
                        methods=[PythonFunction(name="write_report", location=PY_LOCATION)],
                    ),
                    PythonClass(
                        name="B",
                        location=PY_LOCATION,
                        methods=[PythonFunction(name="write_report", location=PY_LOCATION)],
                    ),
                ],
            ),
        ],
        spark_table_writes=[
            SparkTableWrite(
                table_name="gold.report",
                method_name="saveAsTable",
                location=PY_LOCATION,
                function_name="write_report",
            )
        ],
    )

    graph = build_graph("repo-1", model)
    module_id = "repo-1:module:jobs.report"
    table_id = "repo-1:data-table:gold.report"
    assert (module_id, table_id, "WRITES_TO") in {
        (e.source_id, e.target_id, e.type) for e in graph.edges
    }


def test_sql_file_creates_node_and_reads_from_edge_to_data_table() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        sql_files=[SqlFile(name="pipeline/sql/account.sql", location=SourceLocation(
            file_path="pipeline/sql/account.sql"
        ))],
        sql_table_references=[
            SqlTableReference(
                sql_file="pipeline/sql/account.sql",
                table_name="catalog.schema.account_raw",
                access="read",
                statement="SELECT",
                line=1,
            )
        ],
    )

    graph = build_graph("repo-1", model)
    sql_file_id = "repo-1:sql-file:pipeline/sql/account.sql"
    table_id = "repo-1:data-table:catalog.schema.account_raw"
    assert any(n.id == sql_file_id and n.labels == ["SqlFile"] for n in graph.nodes)
    assert any(n.id == table_id and n.labels == ["DataTable"] for n in graph.nodes)
    assert (sql_file_id, table_id, "READS_FROM") in {
        (e.source_id, e.target_id, e.type) for e in graph.edges
    }


def test_sql_file_write_reference_creates_writes_to_edge() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        sql_files=[
            SqlFile(name="pipeline/sql/insert.sql", location=SourceLocation(file_path="pipeline/sql/insert.sql"))
        ],
        sql_table_references=[
            SqlTableReference(
                sql_file="pipeline/sql/insert.sql",
                table_name="catalog.schema.account",
                access="write",
                statement="INSERT_INTO",
                line=1,
            )
        ],
    )

    graph = build_graph("repo-1", model)
    sql_file_id = "repo-1:sql-file:pipeline/sql/insert.sql"
    table_id = "repo-1:data-table:catalog.schema.account"
    assert (sql_file_id, table_id, "WRITES_TO") in {
        (e.source_id, e.target_id, e.type) for e in graph.edges
    }


def test_python_module_registry_reference_creates_loads_sql_edge() -> None:
    # The real sql_registry.py shape: a module-level dict names filenames
    # with no enclosing function - the LOADS_SQL edge is module-level.
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="pipeline.config.sql_registry",
                package="pipeline.config",
                location=SourceLocation(file_path="pipeline/config/sql_registry.py"),
            ),
        ],
        sql_files=[
            SqlFile(
                name="pipeline/sql/account.sql",
                location=SourceLocation(file_path="pipeline/sql/account.sql"),
            )
        ],
        python_sql_file_references=[
            PythonSqlFileReference(
                sql_filename="account.sql",
                location=SourceLocation(file_path="pipeline/config/sql_registry.py"),
                function_name=None,
            )
        ],
    )

    graph = build_graph("repo-1", model)
    module_id = "repo-1:module:pipeline.config.sql_registry"
    sql_file_id = "repo-1:sql-file:pipeline/sql/account.sql"
    assert (module_id, sql_file_id, "LOADS_SQL") in {
        (e.source_id, e.target_id, e.type) for e in graph.edges
    }


def test_python_function_reference_attributes_loads_sql_to_function() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="pipeline.sql.query_loader",
                package="pipeline.sql",
                location=SourceLocation(file_path="pipeline/sql/query_loader.py"),
                functions=[
                    PythonFunction(
                        name="direct_load",
                        location=SourceLocation(file_path="pipeline/sql/query_loader.py"),
                    )
                ],
            ),
        ],
        sql_files=[
            SqlFile(
                name="pipeline/sql/account.sql",
                location=SourceLocation(file_path="pipeline/sql/account.sql"),
            )
        ],
        python_sql_file_references=[
            PythonSqlFileReference(
                sql_filename="pipeline/sql/account.sql",
                location=SourceLocation(file_path="pipeline/sql/query_loader.py"),
                function_name="direct_load",
            )
        ],
    )

    graph = build_graph("repo-1", model)
    function_id = "repo-1:function:pipeline.sql.query_loader.direct_load"
    sql_file_id = "repo-1:sql-file:pipeline/sql/account.sql"
    assert (function_id, sql_file_id, "LOADS_SQL") in {
        (e.source_id, e.target_id, e.type) for e in graph.edges
    }


def test_ambiguous_basename_reference_is_skipped_not_guessed() -> None:
    # Two different .sql files share the basename "account.sql" in
    # different directories - a bare-filename reference to "account.sql"
    # must not arbitrarily pick one.
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="pipeline.config.sql_registry",
                package="pipeline.config",
                location=SourceLocation(file_path="pipeline/config/sql_registry.py"),
            ),
        ],
        sql_files=[
            SqlFile(
                name="pipeline/sql/account.sql",
                location=SourceLocation(file_path="pipeline/sql/account.sql"),
            ),
            SqlFile(
                name="pipeline/sql/legacy/account.sql",
                location=SourceLocation(file_path="pipeline/sql/legacy/account.sql"),
            ),
        ],
        python_sql_file_references=[
            PythonSqlFileReference(
                sql_filename="account.sql",
                location=SourceLocation(file_path="pipeline/config/sql_registry.py"),
                function_name=None,
            )
        ],
    )

    graph = build_graph("repo-1", model)
    assert not any(e.type == "LOADS_SQL" for e in graph.edges)


def test_unresolvable_sql_filename_reference_produces_no_edge() -> None:
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(
                name="pipeline.config.sql_registry",
                package="pipeline.config",
                location=SourceLocation(file_path="pipeline/config/sql_registry.py"),
            ),
        ],
        sql_files=[],
        python_sql_file_references=[
            PythonSqlFileReference(
                sql_filename="does_not_exist.sql",
                location=SourceLocation(file_path="pipeline/config/sql_registry.py"),
                function_name=None,
            )
        ],
    )

    graph = build_graph("repo-1", model)
    assert not any(e.type == "LOADS_SQL" for e in graph.edges)


def test_same_table_referenced_via_spark_and_sql_file_merges_to_one_node() -> None:
    # A table named identically by an inline spark.sql()/DataFrameWriter
    # call and a standalone .sql file must resolve to the exact same
    # DataTable node id - real cross-source lineage merging, not two
    # disconnected facts about "the same" table.
    model = ArchitectureModel(
        language="python",
        framework=None,
        python_modules=[
            PythonModule(name="jobs.report", package="jobs", location=PY_LOCATION),
        ],
        spark_table_reads=[
            SparkTableRead(table_name="catalog.schema.customer", location=PY_LOCATION)
        ],
        sql_files=[
            SqlFile(name="pipeline/sql/x.sql", location=SourceLocation(file_path="pipeline/sql/x.sql"))
        ],
        sql_table_references=[
            SqlTableReference(
                sql_file="pipeline/sql/x.sql",
                table_name="catalog.schema.customer",
                access="write",
                statement="INSERT_INTO",
                line=1,
            )
        ],
    )

    graph = build_graph("repo-1", model)
    table_nodes = [n for n in graph.nodes if n.labels == ["DataTable"]]
    assert len(table_nodes) == 1
    assert table_nodes[0].id == "repo-1:data-table:catalog.schema.customer"
