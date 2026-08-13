"""The internal architecture model.

Every dataclass here is what a `ILanguageParser` implementation produces,
in a language-agnostic shape — `parsers/java/spring_boot_parser.py` is the
only thing that knows these came from `@RestController`/`@FeignClient`/etc.
`graph/builder.py` is the only thing that knows how they become graph nodes.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceLocation:
    """`file_path` is always relative to the repository root — never the
    temp clone directory, which doesn't outlive one indexing run."""

    file_path: str
    line: int | None = None


@dataclass(frozen=True)
class Endpoint:
    http_method: str
    path: str
    handler_method: str
    location: SourceLocation


@dataclass(frozen=True)
class Controller:
    name: str
    package: str
    base_path: str
    location: SourceLocation
    endpoints: list[Endpoint] = field(default_factory=list)


@dataclass(frozen=True)
class SpringService:
    name: str
    package: str
    location: SourceLocation


@dataclass(frozen=True)
class FeignClientMethod:
    http_method: str
    path: str
    method_name: str


@dataclass(frozen=True)
class FeignClient:
    name: str
    package: str
    target_name: str
    location: SourceLocation
    target_url: str | None = None
    methods: list[FeignClientMethod] = field(default_factory=list)


@dataclass(frozen=True)
class KafkaProducerUsage:
    """A `KafkaTemplate.send("topic", ...)` call with a literal topic name.

    A non-literal topic argument (a variable, a method call) can't be
    resolved deterministically without full data-flow analysis, which is
    out of scope — see ADR 0007. Such calls are simply not recorded.
    """

    topic: str
    class_name: str
    method_name: str
    location: SourceLocation


@dataclass(frozen=True)
class KafkaConsumerUsage:
    """A method annotated `@KafkaListener(topics = "...")`."""

    topic: str
    class_name: str
    method_name: str
    location: SourceLocation
    group_id: str | None = None


@dataclass(frozen=True)
class MavenDependency:
    group_id: str
    artifact_id: str
    version: str | None = None
    scope: str | None = None


@dataclass(frozen=True)
class PythonImport:
    """`from module import a, b as c` / `import module`.

    `imported_names` is empty for a bare `import module` statement -
    the module itself is what's imported, not a name from it.
    """

    module: str
    location: SourceLocation
    imported_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PythonFunction:
    """A function or method. `calls` records the literal callee text as
    written (`helper`, `self.baz`, `app.route`) - never resolved to a
    fully-qualified target, matching this codebase's deterministic,
    no-guessing precedent for cross-reference extraction (see ADR 0007).
    """

    name: str
    location: SourceLocation
    decorators: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PythonClass:
    name: str
    location: SourceLocation
    bases: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    methods: list[PythonFunction] = field(default_factory=list)


@dataclass(frozen=True)
class PythonModule:
    """One `.py` file. `name` is its dotted module path from the repo root
    (e.g. `app.services.workflow_service`); `package` is that path minus
    the module's own segment (e.g. `app.services`)."""

    name: str
    package: str
    location: SourceLocation
    imports: list[PythonImport] = field(default_factory=list)
    classes: list[PythonClass] = field(default_factory=list)
    functions: list[PythonFunction] = field(default_factory=list)


@dataclass(frozen=True)
class PythonDependency:
    name: str
    version: str | None = None


@dataclass(frozen=True)
class SparkTableRead:
    """A `spark.read.table("db.table")` / `spark.table("db.table")` call
    with a literal table-name argument, where the call chain resolves back
    to an identifier literally named `spark` - see
    extractors/python/spark.py for why that root check exists and what it
    deliberately does not cover (path-based `.load(...)` reads).
    """

    table_name: str
    location: SourceLocation
    function_name: str | None = None


@dataclass(frozen=True)
class SparkTableWrite:
    """A `<df>.write...saveAsTable("db.table")` /
    `<df>.write...insertInto("db.table")` call with a literal table-name
    argument. `method_name` is whichever of the two matched.
    """

    table_name: str
    method_name: str
    location: SourceLocation
    function_name: str | None = None


@dataclass(frozen=True)
class SqlFile:
    """One `.sql` file discovered anywhere in the repository - independent
    of the repo's detected language (see `indexer/extractors/
    sql_file_extractor.py`, which is run unconditionally, not gated behind
    which `ILanguageParser` ran). `name` is its path relative to the
    repository root, doubling as its identity - `PythonModule` has both a
    dotted `name` and a `location.file_path`; a `.sql` file has no module
    naming convention, so the two are the same string here.
    """

    name: str
    location: SourceLocation


@dataclass(frozen=True)
class SqlTableReference:
    """One table reference found inside a `.sql` file's text - the
    file-based counterpart to `SparkTableRead`/`SparkTableWrite`. See
    `extractors/sql_lineage.py` for what `statement` can be and exactly
    which SQL shapes are (and are not) recognized.
    """

    sql_file: str
    table_name: str
    access: str  # "read" | "write"
    statement: str
    line: int


@dataclass(frozen=True)
class PythonSqlFileReference:
    """A Python module's static, literal reference to a `.sql` file's
    name - either a resolvable `open("...")`-style call, or an entry in a
    module-level literal filename registry (a dict/list). See
    `extractors/python/sql_files.py` for exactly what each rule covers.

    `sql_filename` is the literal text as written in the Python source - a
    bare filename (`"account.sql"`) or a relative path
    (`"pipeline/sql/account.sql"`), never yet resolved against the actual
    `.sql` files discovered in the repository; `indexer/graph/builder.py`
    is what performs that (ambiguity-safe) resolution.
    """

    sql_filename: str
    location: SourceLocation
    function_name: str | None = None


@dataclass
class ArchitectureModel:
    """The aggregate result of parsing one repository."""

    language: str
    framework: str | None
    controllers: list[Controller] = field(default_factory=list)
    services: list[SpringService] = field(default_factory=list)
    feign_clients: list[FeignClient] = field(default_factory=list)
    kafka_producers: list[KafkaProducerUsage] = field(default_factory=list)
    kafka_consumers: list[KafkaConsumerUsage] = field(default_factory=list)
    maven_dependencies: list[MavenDependency] = field(default_factory=list)
    python_modules: list[PythonModule] = field(default_factory=list)
    python_dependencies: list[PythonDependency] = field(default_factory=list)
    spark_table_reads: list[SparkTableRead] = field(default_factory=list)
    spark_table_writes: list[SparkTableWrite] = field(default_factory=list)
    # Populated unconditionally by `indexer/services/indexing_service.py`
    # after language-specific parsing (see that module) - `.sql` files are
    # not gated behind which `ILanguageParser` ran, since they commonly sit
    # alongside Python (or Java) source rather than being "the" language.
    sql_files: list[SqlFile] = field(default_factory=list)
    sql_table_references: list[SqlTableReference] = field(default_factory=list)
    # Populated by `PythonParser` (language-specific: only Python source
    # can statically reference a `.sql` filename the way
    # `extractors/python/sql_files.py` looks for).
    python_sql_file_references: list[PythonSqlFileReference] = field(default_factory=list)
    # The repository's own self-declared package/distribution name (PEP 621
    # `[project.name]` / Poetry `[tool.poetry.name]`) — distinct from its
    # git repository name, and often different from it (see
    # `parsers.python.dependency_parser.parse_python_package_name`'s
    # docstring). `None` for anything without that identity (non-Python
    # repos, or a Python repo with no `pyproject.toml`).
    package_name: str | None = None
