"""Turns an `ArchitectureModel` into the generic `GraphPayload`
`app.graph` persists to Neo4j — the one place that knows how a discovered
Java entity maps to a graph label and relationship type.

Node id scheme: every id is namespaced `f"{repository_id}:{kind}:{key}"`,
so re-indexing the same repository always produces the same ids (MERGE
upserts in place) and ids never collide across repositories.
"""

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.indexer.classification import classify
from app.indexer.models.architecture import ArchitectureModel, PythonFunction


def _classification_properties(
    *, file_path: str, name: str, labels: list[str], class_name: str | None, language: str
) -> dict[str, object]:
    """`is_test`/`confidence`/`symbol_type`/`component_type`/`language` for
    any Component-labeled node, of any language — see
    `app.indexer.classification` for what each means and why. Added to
    every node's `properties` dict alongside its existing fields, so
    nothing that already reads `file_path`/`name` from these nodes needs
    to change; this is purely additive.
    """
    result = classify(file_path=file_path, name=name, labels=labels, class_name=class_name)
    component_type = next((label for label in labels if label != "Component"), "Component")
    return {
        "is_test": result.is_test,
        "confidence": result.confidence,
        "symbol_type": result.symbol_type,
        "component_type": component_type,
        "language": language,
    }


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


def _python_import_node_id(repository_id: str, module: str) -> str:
    return f"{repository_id}:python-import:{module}"


def _data_table_node_id(repository_id: str, table_name: str) -> str:
    return f"{repository_id}:data-table:{table_name}"


def _sql_file_node_id(repository_id: str, sql_file_path: str) -> str:
    return f"{repository_id}:sql-file:{sql_file_path}"


def _config_file_node_id(repository_id: str, config_file_path: str) -> str:
    return f"{repository_id}:config-file:{config_file_path}"


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
        properties.update(
            _classification_properties(
                file_path=function.location.file_path,
                name=function.name,
                labels=["Component", "Function"],
                class_name=class_name,
                language=model.language,
            )
        )
        nodes.append(GraphNode(id=node_id, labels=["Component", "Function"], properties=properties))
        if function.name in function_node_id_by_bare_name:
            function_node_id_by_bare_name[function.name] = None
        else:
            function_node_id_by_bare_name[function.name] = node_id
        pending_calls.append((node_id, function.calls))
        return node_id

    # RFC-0012 — source-level import evidence: an import this repository's
    # own `python_modules` never resolve to one of *its own* modules names
    # something external — a third-party package most of the time, but
    # sometimes another indexed repository's own published package, used
    # but never declared in `pyproject.toml`/`requirements.txt` (see
    # `parsers.python.dependency_parser.parse_python_package_name`'s
    # docstring for the real case this covers). Recorded unconditionally,
    # exactly like `PythonDependency` records every manifest-declared
    # dependency unconditionally — matching against other repositories
    # happens later, in `cross_repo_linker`, never here; an import of `os`
    # or `pandas` simply never matches any indexed repository's name and
    # so never becomes a cross-repository edge, no filtering needed at
    # this layer. Keyed by top-level package (`shared_jobs`, not
    # `shared_jobs.errors`) — that's the distributable unit an indexed
    # repository's own name/package identity can actually match, and it's
    # also the natural, generic deduplication key for requirement #3
    # ("multiple imports from the same target are deduplicated"): every
    # `import shared_jobs` / `from shared_jobs import X` / `from
    # shared_jobs.errors import Y` anywhere in the repository — module-
    # level or deferred inside a function body, `extract_imports` doesn't
    # distinguish — collapses into one node, with every distinct imported
    # name and file path merged into it.
    unresolved_imports: dict[str, dict[str, set[str]]] = {}

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
                continue
            if target_module_id is not None:
                continue  # a module importing itself - not a real signal either way
            top_level = imp.module.split(".", 1)[0]
            if not top_level:
                continue
            entry = unresolved_imports.setdefault(
                top_level, {"imported_names": set(), "file_paths": set()}
            )
            entry["imported_names"].update(imp.imported_names)
            entry["file_paths"].add(module.location.file_path)

        for function in module.functions:
            function_id = register_function(function, f"{module.name}.{function.name}", None)
            edges.append(GraphEdge(source_id=module_id, target_id=function_id, type="CONTAINS"))

        for python_class in module.classes:
            class_id = _class_node_id(repository_id, module.name, python_class.name)
            class_properties: dict[str, object] = {
                "name": python_class.name,
                "package": module.package,
                "file_path": python_class.location.file_path,
                "bases": list(python_class.bases),
                "decorators": list(python_class.decorators),
            }
            class_properties.update(
                _classification_properties(
                    file_path=python_class.location.file_path,
                    name=python_class.name,
                    labels=["Component", "Class"],
                    class_name=None,
                    language=model.language,
                )
            )
            nodes.append(
                GraphNode(id=class_id, labels=["Component", "Class"], properties=class_properties)
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

    for top_level, entry in unresolved_imports.items():
        node_id = _python_import_node_id(repository_id, top_level)
        nodes.append(
            GraphNode(
                id=node_id,
                labels=["PythonImport"],
                properties={
                    # "name", matching every other node type's own
                    # grounding-location convention (`_classification_
                    # properties` and friends) — Knowledge Engine's
                    # structural validators require *some* grounding
                    # (`file_path` or `name`) on every node a hypothesis
                    # touches; a `PythonImport` has no single file_path of
                    # its own (it can be imported from several), so `name`
                    # is the honest one to provide, same as
                    # `PythonDependency` already does.
                    "name": top_level,
                    "module": top_level,
                    "imported_names": sorted(entry["imported_names"]),
                    "file_paths": sorted(entry["file_paths"]),
                },
            )
        )
        # "DEPENDS_ON", matching `PythonDependency`'s own repo-level edge
        # type exactly, not "IMPORTS" — "IMPORTS" already means one of
        # this repository's own modules importing another (see the loop
        # above); reusing it here would make `test_python_unresolved_
        # import_produces_no_edge`'s existing "an external import produces
        # no *module-to-module* IMPORTS edge" assertion ambiguous with
        # this new, deliberately different repo-to-evidence edge.
        edges.append(GraphEdge(source_id=repo_id, target_id=node_id, type="DEPENDS_ON"))

    for source_id, calls in pending_calls:
        for raw_call in calls:
            bare_name = raw_call.rsplit(".", 1)[-1]
            target_id = function_node_id_by_bare_name.get(bare_name)
            if target_id is not None and target_id != source_id:
                edges.append(GraphEdge(source_id=source_id, target_id=target_id, type="CALLS"))

    # Spark table reads/writes are attributed to the specific Function node
    # when `function_name` resolves unambiguously via the exact same
    # `function_node_id_by_bare_name` map the CALLS-resolution loop above
    # already built (a spark.py extractor result only carries a bare
    # function name, ambiguous against a same-named method on an unrelated
    # class - this is the same "ambiguous, so don't guess" question CALLS
    # resolution already answers, reused rather than re-solved); the
    # module is the fallback, coarsest unambiguous owner whenever it
    # doesn't (no `function_name` at all - module-level code - or an
    # ambiguous/unregistered one), matching this codebase's no-guessing
    # precedent for anything it can't resolve deterministically.
    module_id_by_file_path = {
        m.location.file_path: module_node_id_by_name[m.name] for m in model.python_modules
    }

    def _owning_node_id(function_name: str | None, module_id: str) -> str:
        if function_name:
            function_id = function_node_id_by_bare_name.get(function_name)
            if function_id is not None:
                return function_id
        return module_id

    for read in model.spark_table_reads:
        owning_module_id = module_id_by_file_path.get(read.location.file_path)
        if owning_module_id is None:
            continue
        table_id = _data_table_node_id(repository_id, read.table_name)
        nodes.append(
            GraphNode(id=table_id, labels=["DataTable"], properties={"name": read.table_name})
        )
        edges.append(
            GraphEdge(
                source_id=_owning_node_id(read.function_name, owning_module_id),
                target_id=table_id,
                type="READS_FROM",
                properties={"function_name": read.function_name or ""},
            )
        )

    for write in model.spark_table_writes:
        owning_module_id = module_id_by_file_path.get(write.location.file_path)
        if owning_module_id is None:
            continue
        table_id = _data_table_node_id(repository_id, write.table_name)
        nodes.append(
            GraphNode(id=table_id, labels=["DataTable"], properties={"name": write.table_name})
        )
        edges.append(
            GraphEdge(
                source_id=_owning_node_id(write.function_name, owning_module_id),
                target_id=table_id,
                type="WRITES_TO",
                properties={
                    "method_name": write.method_name,
                    "function_name": write.function_name or "",
                },
            )
        )

    # --- `.sql` files: SqlFile nodes, their own READS_FROM/WRITES_TO
    # DataTable edges (same relationship types as the Spark case above -
    # no second table-lineage relationship vocabulary), and LOADS_SQL edges
    # from whichever Python module/function statically named that file. ---
    sql_file_node_id_by_path = {
        f.name: _sql_file_node_id(repository_id, f.name) for f in model.sql_files
    }
    for sql_file in model.sql_files:
        nodes.append(
            GraphNode(
                id=sql_file_node_id_by_path[sql_file.name],
                labels=["SqlFile"],
                properties={"name": sql_file.name, "file_path": sql_file.location.file_path},
            )
        )

    for ref in model.sql_table_references:
        sql_file_id = sql_file_node_id_by_path.get(ref.sql_file)
        if sql_file_id is None:
            continue
        table_id = _data_table_node_id(repository_id, ref.table_name)
        nodes.append(
            GraphNode(id=table_id, labels=["DataTable"], properties={"name": ref.table_name})
        )
        edge_type = "READS_FROM" if ref.access == "read" else "WRITES_TO"
        edges.append(
            GraphEdge(
                source_id=sql_file_id,
                target_id=table_id,
                type=edge_type,
                properties={"statement": ref.statement, "line": ref.line},
            )
        )

    # Bare filename (no directory) -> node id, but only when unambiguous
    # across every `.sql` file discovered in the repository - the same
    # "ambiguous, so don't guess" shape `function_node_id_by_bare_name`
    # already applies to CALLS resolution above, applied here to a second
    # kind of bare-name reference. A registry entry like
    # `SQL_FILE_MAP = {"account": "account.sql"}` names a bare filename,
    # with no directory context to disambiguate it if two different `.sql`
    # files in the repo happen to share that basename.
    sql_file_id_by_basename: dict[str, str | None] = {}
    for sql_file in model.sql_files:
        basename = sql_file.name.rsplit("/", 1)[-1]
        if basename in sql_file_id_by_basename:
            sql_file_id_by_basename[basename] = None
        else:
            sql_file_id_by_basename[basename] = sql_file_node_id_by_path[sql_file.name]

    def _resolve_sql_file_id(sql_filename: str) -> str | None:
        # An exact relative-path match (e.g. "pipeline/sql/account.sql")
        # is unambiguous by construction - prefer it over the basename map.
        exact = sql_file_node_id_by_path.get(sql_filename)
        if exact is not None:
            return exact
        basename = sql_filename.rsplit("/", 1)[-1]
        return sql_file_id_by_basename.get(basename)

    for python_ref in model.python_sql_file_references:
        sql_file_id = _resolve_sql_file_id(python_ref.sql_filename)
        if sql_file_id is None:
            continue
        owning_module_id = module_id_by_file_path.get(python_ref.location.file_path)
        if owning_module_id is None:
            continue
        edges.append(
            GraphEdge(
                source_id=_owning_node_id(python_ref.function_name, owning_module_id),
                target_id=sql_file_id,
                type="LOADS_SQL",
            )
        )

    # --- RFC-0019: config/deployment files (`.yml`/`.yaml`/`.json`) as
    # Component nodes. The flattened key/value text becomes the node's
    # `name` — the only property `_match_text` (app.agents.planning.tools,
    # what every ranking/corroboration function already reads) looks at —
    # so an operational identifier that lives in configuration rather than
    # code flows through the *exact same* machinery every other Component
    # already does (RFC-0015/0017/0018), with zero changes to that
    # machinery. `["Component", "ConfigFile"]` deliberately mirrors the
    # `["Component", "Function"/"Module"/"Class"]` secondary-label shape
    # used everywhere above, NOT the bare `["SqlFile"]`-only shape `.sql`
    # files use just above — that shape is exactly why `.sql` files never
    # participate in ranking today, and this evidence needs to. ---
    config_file_node_id_by_path = {
        c.location.file_path: _config_file_node_id(repository_id, c.location.file_path)
        for c in model.config_files
    }
    for config_file in model.config_files:
        node_id = config_file_node_id_by_path[config_file.location.file_path]
        properties: dict[str, object] = {
            "name": config_file.flattened_text,
            "file_path": config_file.location.file_path,
        }
        properties.update(
            _classification_properties(
                file_path=config_file.location.file_path,
                name=config_file.flattened_text,
                labels=["Component", "ConfigFile"],
                class_name=None,
                language=model.language,
            )
        )
        nodes.append(
            GraphNode(id=node_id, labels=["Component", "ConfigFile"], properties=properties)
        )
        edges.append(GraphEdge(source_id=repo_id, target_id=node_id, type="CONTAINS"))

    # Bare filename -> module id, only when unambiguous across the whole
    # repository — the same "ambiguous, so don't guess" shape
    # `sql_file_id_by_basename` above already applies to `.sql` files,
    # needed here because a config value referencing a file (e.g.
    # `${workspace.file_path}/pipeline/main_pipeline.py`) commonly carries
    # a templated prefix that never matches a real repository-relative
    # path exactly — only the basename is reliably comparable.
    module_id_by_basename: dict[str, str | None] = {}
    for module in model.python_modules:
        basename = module.location.file_path.rsplit("/", 1)[-1]
        if basename in module_id_by_basename:
            module_id_by_basename[basename] = None
        else:
            module_id_by_basename[basename] = module_id_by_file_path.get(module.location.file_path)

    def _resolve_referenced_module_id(referenced_text: str) -> str | None:
        exact = module_id_by_file_path.get(referenced_text)
        if exact is not None:
            return exact
        basename = referenced_text.rstrip("/").rsplit("/", 1)[-1]
        return module_id_by_basename.get(basename)

    for ref in model.config_path_references:
        config_node_id = config_file_node_id_by_path.get(ref.config_file)
        target_module_id = _resolve_referenced_module_id(ref.referenced_text)
        if config_node_id is None or target_module_id is None:
            continue
        edges.append(
            GraphEdge(source_id=config_node_id, target_id=target_module_id, type="REFERENCES")
        )


def build_graph(
    repository_id: str, model: ArchitectureModel, repository_name: str | None = None
) -> GraphPayload:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    repo_id = _repository_node_id(repository_id)
    nodes.append(
        GraphNode(
            id=repo_id,
            labels=["Repository"],
            properties={
                # Every other node type sets "name" (see `_classification_
                # properties` and friends below); the Repository node never
                # did, so anything rendering a node label off `properties.
                # name ?? id` (ImpactedNodesTable, BlastRadiusGraph, PR
                # Review's affected-components list, ...) fell back to the
                # raw `f"{repository_id}:repository"` id — a UUID with a
                # suffix — instead of a human name. `repository_name` is
                # optional (defaults to None, which still writes no "name"
                # key) so this stays callable without a DB-backed
                # `Repository` row, same as before (`index_repository`'s
                # own docstring: "testable without a database at all").
                **({"name": repository_name} if repository_name else {}),
                # RFC-0012 — the repository's own self-declared package
                # identity (PEP 621/Poetry `name`), when it has one, so
                # `cross_repo_linker`'s import-matching rule can match a
                # `from X import Y` elsewhere in the fleet against *this*
                # repository's actual published name, not just its git
                # repository name — the two are commonly different (see
                # `ArchitectureModel.package_name`'s docstring).
                **({"package_name": model.package_name} if model.package_name else {}),
                "language": model.language,
                "framework": model.framework or "",
            },
        )
    )

    # Maps a bare class name (as recorded on Kafka producer/consumer usages,
    # which don't carry package info) to the node id of the Controller/
    # Service/FeignClient it belongs to, if that class was itself discovered.
    component_by_class_name: dict[str, str] = {}

    for controller in model.controllers:
        node_id = _controller_node_id(repository_id, controller.package, controller.name)
        component_by_class_name[controller.name] = node_id
        controller_properties: dict[str, object] = {
            "name": controller.name,
            "package": controller.package,
            "base_path": controller.base_path,
            "file_path": controller.location.file_path,
        }
        controller_properties.update(
            _classification_properties(
                file_path=controller.location.file_path,
                name=controller.name,
                labels=["Component", "Controller"],
                class_name=None,
                language=model.language,
            )
        )
        nodes.append(
            GraphNode(
                id=node_id, labels=["Component", "Controller"], properties=controller_properties
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
        service_properties: dict[str, object] = {
            "name": service.name,
            "package": service.package,
            "file_path": service.location.file_path,
        }
        service_properties.update(
            _classification_properties(
                file_path=service.location.file_path,
                name=service.name,
                labels=["Component", "Service"],
                class_name=None,
                language=model.language,
            )
        )
        nodes.append(
            GraphNode(id=node_id, labels=["Component", "Service"], properties=service_properties)
        )
        edges.append(GraphEdge(source_id=repo_id, target_id=node_id, type="CONTAINS"))

    for feign_client in model.feign_clients:
        node_id = _feign_client_node_id(repository_id, feign_client.package, feign_client.name)
        component_by_class_name[feign_client.name] = node_id
        feign_properties: dict[str, object] = {
            "name": feign_client.name,
            "package": feign_client.package,
            "target_name": feign_client.target_name,
            "target_url": feign_client.target_url or "",
            "file_path": feign_client.location.file_path,
        }
        feign_properties.update(
            _classification_properties(
                file_path=feign_client.location.file_path,
                name=feign_client.name,
                labels=["Component", "FeignClient"],
                class_name=None,
                language=model.language,
            )
        )
        nodes.append(
            GraphNode(id=node_id, labels=["Component", "FeignClient"], properties=feign_properties)
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
            generic_properties: dict[str, object] = {"name": class_name, "file_path": file_path}
            generic_properties.update(
                _classification_properties(
                    file_path=file_path,
                    name=class_name,
                    labels=["Component"],
                    class_name=None,
                    language=model.language,
                )
            )
            nodes.append(
                GraphNode(
                    id=node_id,
                    labels=["Component"],
                    properties=generic_properties,
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

    # A KafkaTopic/DataTable node is appended once per producer/consumer or
    # read/write usage of it, so the same id can appear several times with
    # identical properties - harmless for Neo4j's MERGE, but no reason to
    # send duplicates.
    deduped_nodes = list({node.id: node for node in nodes}.values())

    return GraphPayload(nodes=deduped_nodes, edges=edges)
