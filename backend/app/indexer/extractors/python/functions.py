"""Discovers module-level functions (not methods - those come from
`classes.py`, which reuses `_build_function` on each class body)."""

from tree_sitter import Node

from app.indexer.extractors.python.tree_utils import (
    call_targets_in,
    decorators_of,
    function_name_of,
    unwrap_decorated,
)
from app.indexer.models.architecture import PythonFunction, SourceLocation


def build_function(node: Node, source: bytes, file_path: str) -> PythonFunction:
    """`node` may be a bare `function_definition` or a `decorated_definition`
    wrapping one."""
    definition = unwrap_decorated(node)
    return PythonFunction(
        name=function_name_of(definition, source),
        location=SourceLocation(file_path=file_path),
        decorators=decorators_of(node, source),
        calls=call_targets_in(definition.child_by_field_name("body"), source),
    )


def extract_module_functions(root: Node, source: bytes, file_path: str) -> list[PythonFunction]:
    functions: list[PythonFunction] = []
    for child in root.named_children:
        definition = unwrap_decorated(child) if child.type == "decorated_definition" else child
        if definition.type == "function_definition":
            functions.append(build_function(child, source, file_path))
    return functions
