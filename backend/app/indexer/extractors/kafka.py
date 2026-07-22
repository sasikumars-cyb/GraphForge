"""Discovers Kafka producers and consumers:

- Consumers: methods annotated `@KafkaListener(topics = "..."` or
  `topics = {"a", "b"}`)`.
- Producers: `someKafkaTemplate.send("topic", ...)` calls, where
  `someKafkaTemplate` is a field declared `KafkaTemplate<...>` on the same
  class. Only a literal string topic argument is recorded — see
  `KafkaProducerUsage`'s docstring for why a non-literal one is skipped
  rather than guessed at.
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


def extract_kafka_consumers(root: Node, source: bytes, file_path: str) -> list[KafkaConsumerUsage]:
    consumers: list[KafkaConsumerUsage] = []

    for type_decl in iter_type_declarations(root):
        class_name = class_name_of(type_decl, source)
        location = SourceLocation(file_path=file_path)

        for method in iter_methods(body_of(type_decl)):
            modifiers = modifiers_of(method)
            annotations = annotations_of(modifiers, source)
            if "KafkaListener" not in annotations:
                continue

            listener_node = find_annotation_node(modifiers, "KafkaListener", source)
            args_node = listener_node.child_by_field_name("arguments") if listener_node else None
            topics = annotation_array_values(args_node, "topics", source)
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


def _kafka_template_field_names(class_body: Node | None, source: bytes) -> set[str]:
    names: set[str] = set()
    if class_body is None:
        return names

    for child in class_body.named_children:
        if child.type != "field_declaration":
            continue
        type_text = node_text(child.child_by_field_name("type"), source)
        if not type_text.startswith("KafkaTemplate"):
            continue
        for declarator in child.named_children:
            if declarator.type == "variable_declarator":
                names.add(node_text(declarator.child_by_field_name("name"), source))

    return names


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
        template_fields = _kafka_template_field_names(body_of(type_decl), source)
        if not template_fields:
            continue

        class_name = class_name_of(type_decl, source)
        location = SourceLocation(file_path=file_path)

        for method in iter_methods(body_of(type_decl)):
            method_body = method.child_by_field_name("body")
            if method_body is None:
                continue
            method_name = method_name_of(method, source)

            for invocation in find_nodes_by_type(method_body, "method_invocation"):
                if node_text(invocation.child_by_field_name("name"), source) != "send":
                    continue
                if _receiver_name(invocation, source) not in template_fields:
                    continue

                args = invocation.child_by_field_name("arguments")
                if args is None or not args.named_children:
                    continue
                topic = string_literal_value(args.named_children[0], source)
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
