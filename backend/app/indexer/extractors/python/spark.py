"""Discovers Spark/Databricks table lineage:

- Reads: `spark.read.table("db.table")` / `spark.table("db.table")` /
  `spark.read.format("delta").table("db.table")` - the call chain must
  resolve back to an identifier literally named `spark`, the same
  conservative root-identifier check Kafka's topic extraction uses for the
  `KafkaTemplate`-typed receiver (see extractors/kafka.py). Path-based
  reads (`spark.read.format("delta").load("/mnt/x")`) are not a table name
  and are deliberately not recorded here.
- Writes: `<df>.write...saveAsTable("db.table")` /
  `<df>.write...insertInto("db.table")` - the method name alone is
  unambiguous DataFrameWriter API, so no root-identifier check is needed.
- `spark.sql("...")`: the argument, once resolved to text (see below), is
  handed to `app.indexer.extractors.sql_lineage.extract_sql_table_references`
  - the same generic SQL-text parser `.sql` files go through (see
  `sql_file_extractor.py`) - so a `CREATE TABLE`/`INSERT INTO`/`MERGE INTO`/
  `UPDATE`/`DELETE`/`SELECT ... FROM ... JOIN ...` passed to `spark.sql`
  produces the identical read/write facts a `.sql` file with the same text
  would. This is the fix for the gap the architecture audit found: neither
  `spark.read.table(...)` nor `.saveAsTable(...)` covers the
  `spark.sql(f"CREATE TABLE ...")`/`spark.sql(insert_query)` shapes this
  codebase's own `table_manager.py`/`base_table_loader.py` actually use.

Only a literal (or deterministically-resolvable, see
`literal_resolution.py`) string table-name/SQL-text argument is recorded -
a variable built from a non-literal expression, a config lookup, or an
argument passed in from outside the current function can't be resolved
without full data-flow analysis, so it is skipped rather than guessed at,
matching this codebase's precedent (see KafkaProducerUsage's docstring).
"""

from tree_sitter import Node

from app.indexer.extractors.python.literal_resolution import (
    LocalConstants,
    literal_string_value,
    resolve_string_argument,
)
from app.indexer.extractors.python.tree_utils import node_text, unwrap_decorated
from app.indexer.extractors.sql_lineage import extract_sql_table_references
from app.indexer.models.architecture import SourceLocation, SparkTableRead, SparkTableWrite

_READ_METHODS = {"table"}
_WRITE_METHODS = {"saveAsTable", "insertInto"}
_SQL_METHOD = "sql"
_SPARK_ROOT = "spark"


def _string_literal_value(node: Node | None, source: bytes) -> str | None:
    """A plain `"..."`/`'...'` string node's decoded value, or None for
    anything that isn't a trustworthy literal (an f-string with
    interpolation, a variable, a non-string expression).

    Thin wrapper kept for this module's existing call sites - the actual
    implementation now lives in `literal_resolution.py` so `spark.sql`
    resolution (which needs the same check plus more) shares it rather
    than duplicating it.
    """
    return literal_string_value(node, source)


def _call_root_identifier(node: Node, source: bytes) -> str:
    """Walks a `attribute`/`call` chain down to its leftmost identifier -
    `spark.read.format("delta").table(...)`'s function node unwraps through
    the intervening `.format("delta")` call to reach `spark`."""
    while True:
        if node.type == "attribute":
            obj = node.child_by_field_name("object")
            if obj is None:
                return ""
            node = obj
        elif node.type == "call":
            fn = node.child_by_field_name("function")
            if fn is None:
                return ""
            node = fn
        else:
            break
    return node_text(node, source) if node.type == "identifier" else ""


def _method_name(call: Node, source: bytes) -> str | None:
    function_node = call.child_by_field_name("function")
    if function_node is None or function_node.type != "attribute":
        return None
    attr = function_node.child_by_field_name("attribute")
    return node_text(attr, source) if attr is not None else None


def _first_string_arg(call: Node, source: bytes) -> str | None:
    args = call.child_by_field_name("arguments")
    if args is None or not args.named_children:
        return None
    return _string_literal_value(args.named_children[0], source)


def _enclosing_function_name(node: Node, source: bytes) -> str | None:
    if node.type == "decorated_definition":
        node = unwrap_decorated(node)
    if node.type == "function_definition":
        return node_text(node.child_by_field_name("name"), source)
    return None


def extract_spark_table_reads(root: Node, source: bytes, file_path: str) -> list[SparkTableRead]:
    reads: list[SparkTableRead] = []

    def walk(node: Node, function_name: str | None) -> None:
        own_function = _enclosing_function_name(node, source)
        current_function = own_function if own_function is not None else function_name
        for child in node.named_children:
            if child.type == "call":
                function_node = child.child_by_field_name("function")
                if (
                    function_node is not None
                    and _method_name(child, source) in _READ_METHODS
                    and _call_root_identifier(function_node, source) == _SPARK_ROOT
                ):
                    table_name = _first_string_arg(child, source)
                    if table_name:
                        reads.append(
                            SparkTableRead(
                                table_name=table_name,
                                function_name=current_function,
                                location=SourceLocation(
                                    file_path=file_path, line=child.start_point[0] + 1
                                ),
                            )
                        )
            walk(child, current_function)

    walk(root, None)
    return reads


def extract_spark_table_writes(root: Node, source: bytes, file_path: str) -> list[SparkTableWrite]:
    writes: list[SparkTableWrite] = []

    def walk(node: Node, function_name: str | None) -> None:
        own_function = _enclosing_function_name(node, source)
        current_function = own_function if own_function is not None else function_name
        for child in node.named_children:
            if child.type == "call":
                method_name = _method_name(child, source)
                if method_name in _WRITE_METHODS:
                    table_name = _first_string_arg(child, source)
                    if table_name:
                        writes.append(
                            SparkTableWrite(
                                table_name=table_name,
                                method_name=method_name,
                                function_name=current_function,
                                location=SourceLocation(
                                    file_path=file_path, line=child.start_point[0] + 1
                                ),
                            )
                        )
            walk(child, current_function)

    walk(root, None)
    return writes


def extract_spark_sql_references(
    root: Node, source: bytes, file_path: str
) -> tuple[list[SparkTableRead], list[SparkTableWrite]]:
    """`spark.sql(<text>)` calls: resolves `<text>` (a plain literal, or an
    f-string whose interpolations are all resolvable local constants - see
    `literal_resolution.py`) and runs it through
    `sql_lineage.extract_sql_table_references` for the actual table
    read/write facts.

    Unlike `extract_spark_table_reads`/`_writes` above, this needs each
    call's *enclosing scope* (not just its enclosing function's name) to
    build the `LocalConstants` a resolvable f-string depends on - so this
    walk tracks the scope node (nearest enclosing `function_definition`, or
    the module root when there is none) alongside the function name.

    A `spark.sql(...)` argument that can't be resolved this way (built from
    a function parameter, a config value, a call result - anything other
    than a same-scope literal-assigned local) contributes nothing: no table
    name is invented, matching every other extractor in this file.
    """
    reads: list[SparkTableRead] = []
    writes: list[SparkTableWrite] = []

    def walk(node: Node, function_name: str | None, scope_node: Node) -> None:
        own_function = _enclosing_function_name(node, source)
        current_function = own_function if own_function is not None else function_name
        current_scope = node if node.type == "function_definition" else scope_node

        for child in node.named_children:
            if child.type == "call":
                function_node = child.child_by_field_name("function")
                if (
                    function_node is not None
                    and _method_name(child, source) == _SQL_METHOD
                    and _call_root_identifier(function_node, source) == _SPARK_ROOT
                ):
                    args = child.child_by_field_name("arguments")
                    arg_node = args.named_children[0] if args and args.named_children else None
                    constants = LocalConstants(current_scope, source)
                    sql_text = resolve_string_argument(arg_node, source, constants)
                    if sql_text:
                        location = SourceLocation(
                            file_path=file_path, line=child.start_point[0] + 1
                        )
                        for ref in extract_sql_table_references(sql_text):
                            if ref.access == "read":
                                reads.append(
                                    SparkTableRead(
                                        table_name=ref.table_name,
                                        function_name=current_function,
                                        location=location,
                                    )
                                )
                            else:
                                writes.append(
                                    SparkTableWrite(
                                        table_name=ref.table_name,
                                        method_name=ref.statement,
                                        function_name=current_function,
                                        location=location,
                                    )
                                )
            walk(child, current_function, current_scope)

    walk(root, None, root)
    return reads, writes
