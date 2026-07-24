"""Discovers module-level classes, their base classes, decorators, and
methods (nested classes are not separately extracted - see ADR 0007-style
scoping precedent: this indexer targets application code shape, not a full
symbol table)."""

from tree_sitter import Node

from app.indexer.extractors.python.functions import build_function
from app.indexer.extractors.python.tree_utils import (
    base_class_names,
    class_name_of,
    decorators_of,
    unwrap_decorated,
)
from app.indexer.models.architecture import PythonClass, SourceLocation


def extract_module_classes(root: Node, source: bytes, file_path: str) -> list[PythonClass]:
    classes: list[PythonClass] = []
    for child in root.named_children:
        definition = unwrap_decorated(child) if child.type == "decorated_definition" else child
        if definition.type != "class_definition":
            continue

        methods = []
        body = definition.child_by_field_name("body")
        if body is not None:
            for member in body.named_children:
                member_definition = (
                    unwrap_decorated(member) if member.type == "decorated_definition" else member
                )
                if member_definition.type == "function_definition":
                    methods.append(build_function(member, source, file_path))

        classes.append(
            PythonClass(
                name=class_name_of(definition, source),
                location=SourceLocation(file_path=file_path),
                bases=base_class_names(definition, source),
                decorators=decorators_of(child, source),
                methods=methods,
            )
        )
    return classes
