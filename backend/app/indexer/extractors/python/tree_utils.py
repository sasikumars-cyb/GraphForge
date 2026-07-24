"""Shared tree-sitter node-walking helpers for the Python extractors -
the `app.indexer.extractors.tree_utils` equivalent for Python's grammar
(different node/field names from Java's, so kept separate rather than
forced into the Java-shaped helpers)."""

from collections.abc import Iterator

from tree_sitter import Node

_DEFINITION_TYPES = ("function_definition", "class_definition")


def node_text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def unwrap_decorated(node: Node) -> Node:
    """A `function_definition`/`class_definition` may be wrapped in a
    `decorated_definition` - returns the inner definition node either way."""
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        return inner if inner is not None else node
    return node


def decorators_of(node: Node, source: bytes) -> list[str]:
    """`node` may be a `decorated_definition` or a bare definition (no
    decorators, in which case this returns [])."""
    if node.type != "decorated_definition":
        return []
    decorators: list[str] = []
    for child in node.named_children:
        if child.type == "decorator" and child.named_children:
            decorators.append(node_text(child.named_children[0], source))
    return decorators


def iter_top_level_definitions(scope: Node) -> Iterator[Node]:
    """Every function/class definition directly inside `scope` (a `module`
    or `block` node) - one level deep only, decorator wrapper unwrapped
    transparently. Does not descend into nested definitions."""
    for child in scope.named_children:
        candidate = unwrap_decorated(child) if child.type == "decorated_definition" else child
        if candidate.type in _DEFINITION_TYPES:
            yield child


def class_name_of(class_definition: Node, source: bytes) -> str:
    return node_text(class_definition.child_by_field_name("name"), source)


def function_name_of(function_definition: Node, source: bytes) -> str:
    return node_text(function_definition.child_by_field_name("name"), source)


def base_class_names(class_definition: Node, source: bytes) -> list[str]:
    """Base classes from `class Foo(Base, ns.Other, metaclass=Meta):`.
    `metaclass=...` and other keyword arguments are not base classes and
    are skipped."""
    superclasses = class_definition.child_by_field_name("superclasses")
    if superclasses is None:
        return []
    bases: list[str] = []
    for arg in superclasses.named_children:
        if arg.type in ("identifier", "attribute"):
            bases.append(node_text(arg, source))
    return bases


def call_targets_in(scope: Node | None, source: bytes) -> list[str]:
    """Every `call`'s callee text found anywhere inside `scope`, without
    descending into nested function/class definitions - a call made by an
    inner function belongs to that inner function, not its enclosing one."""
    if scope is None:
        return []
    calls: list[str] = []

    def walk(node: Node) -> None:
        for child in node.named_children:
            if child.type in _DEFINITION_TYPES or (
                child.type == "decorated_definition"
                and unwrap_decorated(child).type in _DEFINITION_TYPES
            ):
                continue
            if child.type == "call":
                function_node = child.child_by_field_name("function")
                if function_node is not None:
                    calls.append(node_text(function_node, source))
            walk(child)

    walk(scope)
    return calls
