"""Discovers a Python module's *static* references to `.sql` files - the
Python-side half of "connect Python code to SQL files" (the other half,
what each `.sql` file itself reads/writes, is
`extractors/sql_file_extractor.py`; `indexer/graph/builder.py` is what
joins the two into `LOADS_SQL` edges).

Two independent, narrow, deterministic rules - no filename is ever
invented; each is exactly as generic as its name (neither is keyed to any
specific repository, package, or filename):

1. **Direct open** - a call to `open(...)` whose sole/path argument
   resolves (via `literal_resolution.py` - a plain literal, or an f-string
   built entirely from same-scope literal-assigned locals) to a string
   ending in `.sql`. Covers `open("pipeline/sql/x.sql")` and
   `open(f"{sql_dir}/x.sql")` when `sql_dir` is itself a resolvable local.
   Does **not** cover a path built with the `/` (`pathlib.Path.
   __truediv__`) operator (`open(SQL_DIR / filename)`) - that's a
   `binary_operator` node, not a string, and resolving it correctly would
   require modeling `pathlib` semantics; skipped rather than guessed at,
   same as any other unresolvable expression in this codebase.

2. **Literal filename registry** - a module-level `name = {...}` or
   `name = [...]` assignment (any name - `SQL_FILE_MAP` is not special-
   cased) whose dict values / list elements are, wherever they are
   themselves plain string literals, filenames ending in `.sql`. This is
   what actually connects a module like `sql_registry.py` (`SQL_FILE_MAP =
   {"account": "account.sql", ...}`) to the files it names - the entries a
   `load_sql(filename)`-style helper elsewhere reads are visible here
   because they're written down as literals, even though tracing that a
   *specific* runtime call to `load_sql` opens *this specific* file would
   require real data-flow analysis this codebase does not do (ADR 0007).

A module satisfying rule 2 gets one `PythonSqlFileReference` per named
`.sql` file, attributed to the module itself (no enclosing function - the
registry is module-level data, not something a function does). A module
satisfying rule 1 gets one `PythonSqlFileReference` per resolved `open()`
call, attributed to its enclosing function when there is one.
"""

from __future__ import annotations

from tree_sitter import Node

from app.indexer.extractors.python.literal_resolution import (
    LocalConstants,
    literal_string_value,
    resolve_string_argument,
)
from app.indexer.extractors.python.tree_utils import node_text, unwrap_decorated
from app.indexer.models.architecture import PythonSqlFileReference, SourceLocation

_OPEN_CALLEES = {"open"}


def _enclosing_function_name(node: Node, source: bytes) -> str | None:
    if node.type == "decorated_definition":
        node = unwrap_decorated(node)
    if node.type == "function_definition":
        return node_text(node.child_by_field_name("name"), source)
    return None


def _is_sql_filename(value: str | None) -> bool:
    return value is not None and value.lower().endswith(".sql") and value != ".sql"


def _direct_open_references(
    root: Node, source: bytes, file_path: str
) -> list[PythonSqlFileReference]:
    references: list[PythonSqlFileReference] = []

    def walk(node: Node, function_name: str | None, scope_node: Node) -> None:
        own_function = _enclosing_function_name(node, source)
        current_function = own_function if own_function is not None else function_name
        current_scope = node if node.type == "function_definition" else scope_node

        for child in node.named_children:
            if child.type == "call":
                callee = child.child_by_field_name("function")
                if callee is not None and callee.type == "identifier":
                    callee_name = node_text(callee, source)
                    if callee_name in _OPEN_CALLEES:
                        args = child.child_by_field_name("arguments")
                        arg_node = (
                            args.named_children[0] if args and args.named_children else None
                        )
                        constants = LocalConstants(current_scope, source)
                        resolved = resolve_string_argument(arg_node, source, constants)
                        if _is_sql_filename(resolved):
                            references.append(
                                PythonSqlFileReference(
                                    sql_filename=resolved,  # type: ignore[arg-type]
                                    function_name=current_function,
                                    location=SourceLocation(
                                        file_path=file_path, line=child.start_point[0] + 1
                                    ),
                                )
                            )
            walk(child, current_function, current_scope)

    walk(root, None, root)
    return references


def _literal_collection_values(node: Node, source: bytes) -> list[str]:
    """Every plain string-literal value inside a `dictionary` (values only,
    not keys) or `list` node - the literal-registry case."""
    values: list[str] = []
    if node.type == "dictionary":
        for pair in node.named_children:
            if pair.type != "pair":
                continue
            value_node = pair.child_by_field_name("value")
            literal = literal_string_value(value_node, source) if value_node else None
            if literal is not None:
                values.append(literal)
    elif node.type == "list":
        for element in node.named_children:
            literal = literal_string_value(element, source)
            if literal is not None:
                values.append(literal)
    return values


def _registry_references(root: Node, source: bytes, file_path: str) -> list[PythonSqlFileReference]:
    references: list[PythonSqlFileReference] = []
    for child in root.named_children:
        statement = child
        if statement.type == "expression_statement" and statement.named_children:
            statement = statement.named_children[0]
        if statement.type != "assignment":
            continue
        value = statement.child_by_field_name("right")
        if value is None or value.type not in ("dictionary", "list"):
            continue
        for literal in _literal_collection_values(value, source):
            if _is_sql_filename(literal):
                references.append(
                    PythonSqlFileReference(
                        sql_filename=literal,
                        function_name=None,
                        location=SourceLocation(
                            file_path=file_path, line=statement.start_point[0] + 1
                        ),
                    )
                )
    return references


def extract_sql_file_references(
    root: Node, source: bytes, file_path: str
) -> list[PythonSqlFileReference]:
    """Both rules combined - the one function `python_parser.py` calls per
    file. Order (direct-open references first, then registry references)
    is not meaningful; callers should not depend on it.
    """
    return _direct_open_references(root, source, file_path) + _registry_references(
        root, source, file_path
    )
