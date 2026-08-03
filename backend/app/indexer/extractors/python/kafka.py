"""Discovers Kafka producer/consumer usage that goes through this fleet's
shared SDK wrapper (`shared_python_sdk.kafka_client.EventProducer`/
`EventConsumer`) - the delegation pattern every Python repository actually
publishes/subscribes through, rather than a raw confluent-kafka call.

Detected:
- Producer: `<name>.publish(<topic>, ...)` where `<name>` was assigned
  from an `EventProducer(...)` constructor call anywhere in the module (a
  module-level singleton is this fleet's convention).
- Consumer: `EventConsumer(..., topics=<topics>, group_id=...)`
  constructor calls - `topics` may be a list literal or a name bound to
  one.

A topic argument may be a string literal or a name resolving to a
module-level constant (`NAME = "literal"` or `NAMES = ["a", "b"]`) - one
level of constant resolution, no further data-flow analysis, matching
this codebase's existing precedent (see `KafkaProducerUsage`'s docstring
and `extractors/kafka.py`, the Java equivalent of this constant
resolution).
"""

from tree_sitter import Node

from app.indexer.extractors.python.tree_utils import node_text, unwrap_decorated
from app.indexer.models.architecture import KafkaConsumerUsage, KafkaProducerUsage, SourceLocation

_PRODUCER_CONSTRUCTOR = "EventProducer"
_CONSUMER_CONSTRUCTOR = "EventConsumer"


def _string_literal_value(node: Node | None, source: bytes) -> str | None:
    """A plain `"..."`/`'...'` string node's decoded value - an f-string
    with interpolation, or anything not a string node, is not a
    trustworthy literal and returns None (see `extractors/python/spark.py`,
    which uses this same check for the same reason)."""
    if node is None or node.type != "string":
        return None
    if any(child.type == "interpolation" for child in node.named_children):
        return None
    content = next((c for c in node.named_children if c.type == "string_content"), None)
    return node_text(content, source) if content is not None else ""


def _call_callee_name(call: Node, source: bytes) -> str:
    """Bare `Name(...)` -> "Name"; `module.Name(...)` -> "Name" (the last
    segment of an attribute chain)."""
    function_node = call.child_by_field_name("function")
    if function_node is None:
        return ""
    if function_node.type == "attribute":
        attr = function_node.child_by_field_name("attribute")
        return node_text(attr, source) if attr is not None else ""
    return node_text(function_node, source)


def _enclosing_function_name(node: Node, source: bytes) -> str | None:
    if node.type == "decorated_definition":
        node = unwrap_decorated(node)
    if node.type == "function_definition":
        return node_text(node.child_by_field_name("name"), source)
    return None


def _module_constants(root: Node, source: bytes) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Module-level `NAME = "literal"` and `NAMES = ["a", "b"]`
    assignments - one level deep; a list built from other names (rather
    than string literals) is not resolved."""
    strings: dict[str, str] = {}
    lists: dict[str, list[str]] = {}

    for child in root.named_children:
        if child.type != "expression_statement" or not child.named_children:
            continue
        assignment = child.named_children[0]
        if assignment.type != "assignment":
            continue
        left = assignment.child_by_field_name("left")
        right = assignment.child_by_field_name("right")
        if left is None or right is None or left.type != "identifier":
            continue
        name = node_text(left, source)

        literal = _string_literal_value(right, source)
        if literal is not None:
            strings[name] = literal
            continue
        if right.type == "list":
            values = [_string_literal_value(el, source) for el in right.named_children]
            if values and all(v is not None for v in values):
                lists[name] = list(values)  # type: ignore[arg-type]

    return strings, lists


def _resolve_topic(node: Node, source: bytes, strings: dict[str, str]) -> str | None:
    literal = _string_literal_value(node, source)
    if literal is not None:
        return literal
    if node.type == "identifier":
        return strings.get(node_text(node, source))
    return None


def _resolve_topics(
    node: Node, source: bytes, strings: dict[str, str], lists: dict[str, list[str]]
) -> list[str]:
    if node.type == "list":
        values = [_resolve_topic(el, source, strings) for el in node.named_children]
        return [v for v in values if v is not None]
    if node.type == "identifier":
        return lists.get(node_text(node, source), [])
    return []


def _producer_variable_names(root: Node, source: bytes) -> set[str]:
    """Every name assigned from an `EventProducer(...)` constructor call,
    anywhere in the module - this fleet's convention is a module-level
    singleton, but the search isn't restricted to module scope."""
    names: set[str] = set()

    def walk(node: Node) -> None:
        if node.type == "assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if (
                left is not None
                and left.type == "identifier"
                and right is not None
                and right.type == "call"
                and _call_callee_name(right, source) == _PRODUCER_CONSTRUCTOR
            ):
                names.add(node_text(left, source))
        for child in node.named_children:
            walk(child)

    walk(root)
    return names


def extract_kafka_producers(
    root: Node, source: bytes, file_path: str, module_name: str
) -> list[KafkaProducerUsage]:
    producers: list[KafkaProducerUsage] = []
    producer_vars = _producer_variable_names(root, source)
    if not producer_vars:
        return producers

    strings, _lists = _module_constants(root, source)

    def walk(node: Node, function_name: str | None) -> None:
        own_function = _enclosing_function_name(node, source)
        current_function = own_function if own_function is not None else function_name
        for child in node.named_children:
            if child.type == "call":
                function_node = child.child_by_field_name("function")
                if (
                    function_node is not None
                    and function_node.type == "attribute"
                    and node_text(function_node.child_by_field_name("object"), source)
                    in producer_vars
                    and node_text(function_node.child_by_field_name("attribute"), source)
                    == "publish"
                ):
                    args = child.child_by_field_name("arguments")
                    if args is not None and args.named_children:
                        topic = _resolve_topic(args.named_children[0], source, strings)
                        if topic is not None:
                            producers.append(
                                KafkaProducerUsage(
                                    topic=topic,
                                    class_name=module_name,
                                    method_name=current_function or "",
                                    location=SourceLocation(
                                        file_path=file_path, line=child.start_point[0] + 1
                                    ),
                                )
                            )
            walk(child, current_function)

    walk(root, None)
    return producers


def extract_kafka_consumers(
    root: Node, source: bytes, file_path: str, module_name: str
) -> list[KafkaConsumerUsage]:
    consumers: list[KafkaConsumerUsage] = []
    strings, lists = _module_constants(root, source)

    def walk(node: Node, function_name: str | None) -> None:
        own_function = _enclosing_function_name(node, source)
        current_function = own_function if own_function is not None else function_name
        for child in node.named_children:
            if child.type == "call" and _call_callee_name(child, source) == _CONSUMER_CONSTRUCTOR:
                args = child.child_by_field_name("arguments")
                topics_node = None
                group_id_node = None
                if args is not None:
                    for arg in args.named_children:
                        if arg.type != "keyword_argument":
                            continue
                        key = node_text(arg.child_by_field_name("name"), source)
                        if key == "topics":
                            topics_node = arg.child_by_field_name("value")
                        elif key == "group_id":
                            group_id_node = arg.child_by_field_name("value")

                topics = _resolve_topics(topics_node, source, strings, lists) if topics_node else []
                group_id = (
                    _string_literal_value(group_id_node, source)
                    if group_id_node is not None
                    else None
                )

                for topic in topics:
                    consumers.append(
                        KafkaConsumerUsage(
                            topic=topic,
                            class_name=module_name,
                            method_name=current_function or "",
                            location=SourceLocation(
                                file_path=file_path, line=child.start_point[0] + 1
                            ),
                            group_id=group_id,
                        )
                    )
            walk(child, current_function)

    walk(root, None)
    return consumers
