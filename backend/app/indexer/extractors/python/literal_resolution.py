"""Deterministic string-literal resolution — shared by `spark.py` (resolving
a `spark.sql(...)` argument) and `sql_files.py` (resolving a file-open
argument), so both extractors that need "is this expression provably a
literal string" agree on exactly one answer to that question instead of
drifting apart.

Resolves exactly two shapes, matching this codebase's existing no-guessing
precedent (ADR 0007 - see `spark.py`'s own module docstring):

1. A plain (or f-string-with-no-interpolation) string literal - already
   handled by `_string_literal_value`-style checks elsewhere; re-exposed
   here as `literal_string_value` so callers share one implementation.
2. An f-string whose every interpolation is a *bare identifier* that itself
   resolves, via `LocalConstants`, to an unambiguous, unreassigned literal
   string assigned earlier in the same enclosing scope - e.g.:

       table = "catalog.schema.customer"
       spark.sql(f"SELECT * FROM {table}")

   Anything else - a method call, an attribute access, an f-string format
   spec, an identifier assigned more than once, an identifier never
   assigned to a literal at all - fails resolution and returns None. There
   is deliberately no fallback that invents a value: a caller receiving
   None must skip the reference, never guess at it.

This is intentionally *not* general data-flow analysis: only simple,
same-scope, single-assignment locals are tracked. A value threaded through
a function call, a class attribute, or a different scope is out of reach
by design - consistent with every other deterministic extractor in this
codebase.
"""

from __future__ import annotations

from tree_sitter import Node


def literal_string_value(node: Node | None, source: bytes) -> str | None:
    """A plain `"..."`/`'...'`/`\"\"\"...\"\"\"` string node's decoded value,
    or None for anything that isn't a trustworthy literal (an f-string with
    at least one interpolation, or a non-string expression).

    Also resolves Python's *implicit* adjacent-string-literal concatenation
    (`"a" "b"` -> a single `concatenated_string` node, e.g.
    `spark.sql("INSERT INTO t " "SELECT * FROM s")` split across lines for
    readability) - joining plain literals this way is exact, not a guess
    (no runtime value is involved), so it's resolved the same as a single
    literal. Fails (returns None) the moment any one piece isn't itself a
    plain literal - a mix of a literal and an f-string interpolation is
    handled by `resolve_string_argument` below, not here.
    """
    if node is None:
        return None
    if node.type == "concatenated_string":
        parts: list[str] = []
        for child in node.named_children:
            value = literal_string_value(child, source)
            if value is None:
                return None
            parts.append(value)
        return "".join(parts)
    if node.type != "string":
        return None
    if any(child.type == "interpolation" for child in node.named_children):
        return None
    return "".join(
        _node_text(child, source)
        for child in node.named_children
        if child.type == "string_content"
    )


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class LocalConstants:
    """`identifier -> literal value` for a single function (or module-level)
    scope, built from every top-level `name = "<literal>"` assignment
    directly inside that scope's block.

    An identifier assigned more than once is deliberately recorded as
    unresolvable (`None`) rather than picking one of the assignments - this
    mirrors `function_node_id_by_bare_name`'s "ambiguous, so don't guess"
    handling in `indexer/graph/builder.py`. Only assignments whose RHS is
    itself a plain literal are tracked; `x = some_call()` or `x = y` are not
    - the name simply never enters the map, which resolution treats
    identically to "assigned more than once" (both mean "not usable").
    """

    # Node types that share the *same* Python scope as their enclosing
    # function - Python has no block scoping, so an assignment inside an
    # `if`/`for`/`while`/`try`/`with` body is a reassignment candidate for
    # this exact same `LocalConstants`, not a separate scope. A nested
    # `function_definition`/`lambda`/`class_definition` IS a different
    # scope and is deliberately not descended into - its own locals don't
    # leak into the outer scope's constant map.
    _SAME_SCOPE_CONTAINERS = frozenset(
        {
            "block",
            "if_statement",
            "elif_clause",
            "else_clause",
            "for_statement",
            "while_statement",
            "try_statement",
            "except_clause",
            "finally_clause",
            "with_statement",
            "with_clause",
        }
    )

    def __init__(self, scope_node: Node, source: bytes) -> None:
        self._values: dict[str, str | None] = {}
        block = scope_node
        if scope_node.type == "function_definition":
            block = scope_node.child_by_field_name("body") or scope_node
        self._collect(block, source)

    def _collect(self, node: Node, source: bytes) -> None:
        for child in node.named_children:
            statement = child
            if statement.type == "expression_statement" and statement.named_children:
                statement = statement.named_children[0]

            if statement.type == "assignment":
                target = statement.child_by_field_name("left")
                value = statement.child_by_field_name("right")
                if target is not None and target.type == "identifier" and value is not None:
                    name = _node_text(target, source)
                    literal = literal_string_value(value, source)
                    if name in self._values:
                        # Reassigned (even to another literal, even only on
                        # one conditional branch) - which value is "the"
                        # value at any later use site isn't knowable
                        # without tracking control flow, so treat as
                        # unresolvable rather than picking either one.
                        self._values[name] = None
                    else:
                        self._values[name] = literal

            if child.type in self._SAME_SCOPE_CONTAINERS:
                self._collect(child, source)

    def get(self, name: str) -> str | None:
        return self._values.get(name)


def resolve_f_string(string_node: Node, source: bytes, constants: LocalConstants) -> str | None:
    """Resolve an f-string whose every interpolation is a bare identifier
    resolvable via `constants`. Returns the fully-substituted text, or None
    the moment any interpolation can't be resolved this way (a call, an
    attribute access, a format spec, an unresolvable/reassigned name).
    """
    if string_node.type != "string":
        return None
    parts: list[str] = []
    for child in string_node.named_children:
        if child.type == "string_content":
            parts.append(_node_text(child, source))
        elif child.type == "interpolation":
            expr = next(
                (c for c in child.named_children if c.type not in ("format_specifier", "type_conversion")),
                None,
            )
            if expr is None or expr.type != "identifier":
                return None
            value = constants.get(_node_text(expr, source))
            if value is None:
                return None
            parts.append(value)
        # `string_start`/`string_end` and any other structural child
        # contribute no text.
    return "".join(parts)


def resolve_string_argument(
    node: Node | None, source: bytes, constants: LocalConstants
) -> str | None:
    """The one entry point callers should use: resolves a plain literal
    first, falls back to `resolve_f_string` for an f-string, and - for a
    `concatenated_string` (implicit adjacent-literal concatenation, e.g. a
    long `spark.sql(...)` argument split across lines) - resolves and
    joins every piece, each of which may independently be a plain literal
    or a resolvable f-string. Returns None for anything that can't be
    resolved this way (a variable, a call, a non-string expression, or any
    one piece of a concatenation failing) - exactly the "skip rather than
    guess" contract both call sites need.
    """
    if node is None:
        return None
    if node.type == "concatenated_string":
        parts: list[str] = []
        for child in node.named_children:
            value = resolve_string_argument(child, source, constants)
            if value is None:
                return None
            parts.append(value)
        return "".join(parts)
    if node.type != "string":
        return None
    plain = literal_string_value(node, source)
    if plain is not None:
        return plain
    return resolve_f_string(node, source, constants)
