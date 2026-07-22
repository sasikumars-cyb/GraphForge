"""Discovers `@Service`-annotated classes."""

from tree_sitter import Node

from app.indexer.extractors.tree_utils import (
    annotations_of,
    class_name_of,
    iter_type_declarations,
    modifiers_of,
    package_name,
)
from app.indexer.models.architecture import SourceLocation, SpringService


def extract_services(root: Node, source: bytes, file_path: str) -> list[SpringService]:
    services: list[SpringService] = []
    package = package_name(root, source)

    for type_decl in iter_type_declarations(root):
        annotations = annotations_of(modifiers_of(type_decl), source)
        if "Service" not in annotations:
            continue

        services.append(
            SpringService(
                name=class_name_of(type_decl, source),
                package=package,
                location=SourceLocation(file_path=file_path),
            )
        )

    return services
