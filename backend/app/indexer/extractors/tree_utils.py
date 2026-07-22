"""Shared tree-sitter node-walking helpers every extractor builds on.

Deliberately plain recursive traversal + field lookups, not tree-sitter's
query DSL — Java annotation shapes (`@Foo`, `@Foo("x")`,
`@Foo(key = "x", other = Bar.BAZ)`) vary enough that explicit Python is
easier to read and test than an equivalent S-expression query.
"""

from collections.abc import Iterator

from tree_sitter import Node

_TYPE_DECLARATIONS = ("class_declaration", "interface_declaration")


def node_text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def find_nodes_by_type(root: Node, node_type: str) -> Iterator[Node]:
    """Every descendant of `root` (root included) with this node type,
    regardless of nesting depth."""
    if root.type == node_type:
        yield root
    for child in root.named_children:
        yield from find_nodes_by_type(child, node_type)


def iter_type_declarations(root: Node) -> Iterator[Node]:
    """Every class/interface declaration anywhere in the file."""
    for node_type in _TYPE_DECLARATIONS:
        yield from find_nodes_by_type(root, node_type)


def package_name(root: Node, source: bytes) -> str:
    for child in root.named_children:
        if child.type == "package_declaration" and child.named_children:
            return node_text(child.named_children[0], source)
    return ""


def string_literal_value(node: Node | None, source: bytes) -> str | None:
    if node is None or node.type != "string_literal":
        return None
    return node_text(node, source).strip('"')


def enum_constant_value(node: Node | None, source: bytes) -> str | None:
    """For values like `RequestMethod.GET` — returns the part after the dot."""
    if node is None or node.type != "field_access":
        return None
    field = node.child_by_field_name("field")
    return node_text(field, source) if field is not None else None


def annotation_args(args_node: Node | None, source: bytes) -> dict[str, str]:
    """`annotation_argument_list` -> {key: value}. Only string-literal and
    simple enum-constant values are resolved (deterministic); anything else
    (a method call, a field reference to a non-enum constant) is skipped -
    not guessed at.
    """
    result: dict[str, str] = {}
    if args_node is None:
        return result

    for child in args_node.named_children:
        if child.type == "element_value_pair":
            key = node_text(child.child_by_field_name("key"), source)
            value_node = child.child_by_field_name("value")
            value = string_literal_value(value_node, source) or enum_constant_value(
                value_node, source
            )
            if value is not None:
                result[key] = value
        elif child.type == "string_literal":
            # Shorthand form: @Foo("bar") means @Foo(value = "bar").
            value = string_literal_value(child, source)
            if value is not None:
                result["value"] = value

    return result


def annotation_array_values(args_node: Node | None, key: str, source: bytes) -> list[str]:
    """For an argument that may be an array, e.g. `topics = {"a", "b"}` -
    returns every string-literal element. A single string-literal value
    (not an array) is returned as a one-element list. Non-string elements
    are silently skipped (deterministic: no partial guesses).
    """
    if args_node is None:
        return []

    for child in args_node.named_children:
        value_node: Node | None = None
        if child.type == "element_value_pair":
            if node_text(child.child_by_field_name("key"), source) == key:
                value_node = child.child_by_field_name("value")
        elif key == "value" and child.type in (
            "string_literal",
            "element_value_array_initializer",
        ):
            value_node = child

        if value_node is None:
            continue
        if value_node.type == "string_literal":
            single = string_literal_value(value_node, source)
            return [single] if single is not None else []
        if value_node.type == "element_value_array_initializer":
            values = (string_literal_value(c, source) for c in value_node.named_children)
            return [v for v in values if v is not None]

    return []


def annotations_of(modifiers_node: Node | None, source: bytes) -> dict[str, dict[str, str]]:
    """{annotation_name: {arg_key: arg_value}} for every annotation on a
    `modifiers` node (a class, interface, method, or field declaration's
    modifiers). A marker annotation (`@Foo`, no args) maps to `{}`.
    """
    result: dict[str, dict[str, str]] = {}
    if modifiers_node is None:
        return result

    for child in modifiers_node.named_children:
        if child.type == "marker_annotation":
            name = node_text(child.child_by_field_name("name"), source)
            result[name] = {}
        elif child.type == "annotation":
            name = node_text(child.child_by_field_name("name"), source)
            result[name] = annotation_args(child.child_by_field_name("arguments"), source)

    return result


def find_annotation_node(modifiers_node: Node | None, name: str, source: bytes) -> Node | None:
    """The raw annotation node for `name` on `modifiers_node`, if present -
    for callers that need more than `annotations_of`'s resolved dict, e.g.
    `annotation_array_values` for a `topics = {"a", "b"}` argument.
    """
    if modifiers_node is None:
        return None
    for child in modifiers_node.named_children:
        if (
            child.type in ("marker_annotation", "annotation")
            and node_text(child.child_by_field_name("name"), source) == name
        ):
            return child
    return None


def class_name_of(type_declaration: Node, source: bytes) -> str:
    return node_text(type_declaration.child_by_field_name("name"), source)


def modifiers_of(declaration: Node) -> Node | None:
    for child in declaration.children:
        if child.type == "modifiers":
            return child
    return None


def body_of(type_declaration: Node) -> Node | None:
    return type_declaration.child_by_field_name("body")


def iter_methods(class_or_interface_body: Node | None) -> Iterator[Node]:
    if class_or_interface_body is None:
        return
    for child in class_or_interface_body.named_children:
        if child.type == "method_declaration":
            yield child


def method_name_of(method_declaration: Node, source: bytes) -> str:
    return node_text(method_declaration.child_by_field_name("name"), source)
