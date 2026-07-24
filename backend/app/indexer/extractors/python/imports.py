"""Discovers `import`/`from ... import ...` statements at any depth in a
module (top-level and inside functions/classes - a deliberately simple,
depth-agnostic scan since Python permits imports anywhere)."""

from tree_sitter import Node

from app.indexer.extractors.python.tree_utils import node_text
from app.indexer.models.architecture import PythonImport, SourceLocation


def _imported_name(name_node: Node, source: bytes) -> str:
    if name_node.type == "aliased_import":
        original = name_node.child_by_field_name("name")
        return node_text(original, source)
    return node_text(name_node, source)


def extract_imports(root: Node, source: bytes, file_path: str) -> list[PythonImport]:
    imports: list[PythonImport] = []

    def walk(node: Node) -> None:
        if node.type == "import_statement":
            for name_node in node.children_by_field_name("name"):
                module = _imported_name(name_node, source)
                imports.append(
                    PythonImport(module=module, location=SourceLocation(file_path=file_path))
                )
        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            module = node_text(module_node, source)
            has_wildcard = any(child.type == "wildcard_import" for child in node.named_children)
            imported_names = (
                ["*"]
                if has_wildcard
                else [
                    _imported_name(name_node, source)
                    for name_node in node.children_by_field_name("name")
                ]
            )
            imports.append(
                PythonImport(
                    module=module,
                    location=SourceLocation(file_path=file_path),
                    imported_names=imported_names,
                )
            )
        else:
            for child in node.named_children:
                walk(child)

    walk(root)
    return imports
