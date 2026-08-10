"""Discovers Kafka producers and consumers:

- Consumers: methods annotated `@KafkaListener(topics = "..."`,
  `topics = {"a", "b"}`, or `topics = TOPIC` where `TOPIC` is a
  same-class `static final String` constant.
- Producers: `someTemplate.send("topic", ...)` / `somePublisher.publish(
  "topic", ...)` calls, where the receiver is a field declared either
  `KafkaTemplate<...>` (raw) or the shared SDK's `EventPublisher`
  (delegated) on the same class. The topic argument may be a literal
  string or a same-class `static final String` constant.

Constant resolution is intentionally same-class-only, one level deep - no
cross-class or cross-file lookup, matching `KafkaProducerUsage`'s existing
precedent that anything requiring real data-flow analysis is skipped
rather than guessed at (see ADR 0007).
"""

from tree_sitter import Node

from app.indexer.extractors.tree_utils import (
    annotation_array_values,
    annotations_of,
    body_of,
    class_name_of,
    find_annotation_node,
    find_nodes_by_type,
    iter_methods,
    iter_type_declarations,
    method_name_of,
    modifiers_of,
    node_text,
    string_literal_value,
)
from app.indexer.models.architecture import KafkaConsumerUsage, KafkaProducerUsage, SourceLocation


def _string_constant_fields(class_body: Node | None, source: bytes) -> dict[str, str]:
    """`public static final String NAME = "literal";` fields declared
    directly on this class - same-class-only, matching this module's
    constant-resolution precedent."""
    constants: dict[str, str] = {}
    if class_body is None:
        return constants

    for child in class_body.named_children:
        if child.type != "field_declaration":
            continue
        modifiers_text = node_text(modifiers_of(child), source)
        if "static" not in modifiers_text or "final" not in modifiers_text:
            continue
        if node_text(child.child_by_field_name("type"), source) != "String":
            continue
        for declarator in child.named_children:
            if declarator.type != "variable_declarator":
                continue
            name = node_text(declarator.child_by_field_name("name"), source)
            value = string_literal_value(declarator.child_by_field_name("value"), source)
            if value is not None:
                constants[name] = value

    return constants


def extract_kafka_consumers(root: Node, source: bytes, file_path: str) -> list[KafkaConsumerUsage]:
    consumers: list[KafkaConsumerUsage] = []

    for type_decl in iter_type_declarations(root):
        class_name = class_name_of(type_decl, source)
        location = SourceLocation(file_path=file_path)
        constants = _string_constant_fields(body_of(type_decl), source)

        for method in iter_methods(body_of(type_decl)):
            modifiers = modifiers_of(method)
            annotations = annotations_of(modifiers, source)
            if "KafkaListener" not in annotations:
                continue

            listener_node = find_annotation_node(modifiers, "KafkaListener", source)
            args_node = listener_node.child_by_field_name("arguments") if listener_node else None
            topics = annotation_array_values(args_node, "topics", source, constants=constants)
            group_id = annotations["KafkaListener"].get("groupId")
            method_name = method_name_of(method, source)

            for topic in topics:
                consumers.append(
                    KafkaConsumerUsage(
                        topic=topic,
                        class_name=class_name,
                        method_name=method_name,
                        location=location,
                        group_id=group_id,
                    )
                )

    return consumers


# A field's declared type -> the send method name expected on it. Covers
# both a raw KafkaTemplate and the shared SDK's EventPublisher, which
# wraps one - services in this fleet delegate through the SDK rather than
# calling KafkaTemplate directly, and both are legitimate "this class
# produces to Kafka" evidence.
_PRODUCER_FIELD_TYPE_METHODS = {
    "KafkaTemplate": "send",
    "EventPublisher": "publish",
}


def _producer_field_methods(class_body: Node | None, source: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    if class_body is None:
        return fields

    for child in class_body.named_children:
        if child.type != "field_declaration":
            continue
        type_text = node_text(child.child_by_field_name("type"), source)
        method = next(
            (
                m
                for prefix, m in _PRODUCER_FIELD_TYPE_METHODS.items()
                if type_text.startswith(prefix)
            ),
            None,
        )
        if method is None:
            continue
        for declarator in child.named_children:
            if declarator.type == "variable_declarator":
                fields[node_text(declarator.child_by_field_name("name"), source)] = method

    return fields


def _receiver_name(invocation: Node, source: bytes) -> str:
    """`kafkaTemplate.send(...)` -> "kafkaTemplate"; `this.kafkaTemplate.send(...)`
    -> "kafkaTemplate" (the last segment of a field access chain)."""
    obj = invocation.child_by_field_name("object")
    if obj is None:
        return ""
    if obj.type == "field_access":
        field = obj.child_by_field_name("field")
        return node_text(field, source)
    return node_text(obj, source)


def extract_kafka_producers(root: Node, source: bytes, file_path: str) -> list[KafkaProducerUsage]:
    producers: list[KafkaProducerUsage] = []

    for type_decl in iter_type_declarations(root):
        class_body = body_of(type_decl)
        field_methods = _producer_field_methods(class_body, source)
        if not field_methods:
            continue

        constants = _string_constant_fields(class_body, source)
        class_name = class_name_of(type_decl, source)
        location = SourceLocation(file_path=file_path)

        for method in iter_methods(class_body):
            method_body = method.child_by_field_name("body")
            if method_body is None:
                continue
            method_name = method_name_of(method, source)

            for invocation in find_nodes_by_type(method_body, "method_invocation"):
                invoked_name = node_text(invocation.child_by_field_name("name"), source)
                expected_method = field_methods.get(_receiver_name(invocation, source))
                if expected_method is None or invoked_name != expected_method:
                    continue

                args = invocation.child_by_field_name("arguments")
                if args is None or not args.named_children:
                    continue
                topic_node = args.named_children[0]
                topic = string_literal_value(topic_node, source)
                if topic is None and topic_node.type == "identifier":
                    topic = constants.get(node_text(topic_node, source))
                if topic is None:
                    continue

                producers.append(
                    KafkaProducerUsage(
                        topic=topic,
                        class_name=class_name,
                        method_name=method_name,
                        location=location,
                    )
                )

    return producers
