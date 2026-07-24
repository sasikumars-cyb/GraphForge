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
