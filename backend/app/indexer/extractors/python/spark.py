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

Only a literal string table-name argument is recorded - an f-string, a
variable, or a config lookup can't be resolved deterministically without
full data-flow analysis, so it is skipped rather than guessed at, matching
this codebase's precedent (see KafkaProducerUsage's docstring).
"""

from tree_sitter import Node

from app.indexer.extractors.python.tree_utils import node_text, unwrap_decorated
from app.indexer.models.architecture import SourceLocation, SparkTableRead, SparkTableWrite

_READ_METHODS = {"table"}
_WRITE_METHODS = {"saveAsTable", "insertInto"}
_SPARK_ROOT = "spark"


def _string_literal_value(node: Node | None, source: bytes) -> str | None:
    """A plain `"..."`/`'...'` string node's decoded value, or None for
    anything that isn't a trustworthy literal (an f-string with
    interpolation, a variable, a non-string expression)."""
    if node is None or node.type != "string":
        return None
    if any(child.type == "interpolation" for child in node.named_children):
        return None
    content = next((c for c in node.named_children if c.type == "string_content"), None)
    return node_text(content, source) if content is not None else ""


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
