"""Discovers `@FeignClient` interfaces and the remote endpoints their
declared methods map to.
"""

from tree_sitter import Node

from app.indexer.extractors.tree_utils import (
    annotations_of,
    body_of,
    class_name_of,
    iter_methods,
    iter_type_declarations,
    method_name_of,
    modifiers_of,
    package_name,
)
from app.indexer.models.architecture import FeignClient, FeignClientMethod, SourceLocation

_HTTP_METHOD_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": "ANY",
}


def extract_feign_clients(root: Node, source: bytes, file_path: str) -> list[FeignClient]:
    clients: list[FeignClient] = []
    package = package_name(root, source)

    for type_decl in iter_type_declarations(root):
        annotations = annotations_of(modifiers_of(type_decl), source)
        if "FeignClient" not in annotations:
            continue

        args = annotations["FeignClient"]
        target_name = args.get("name") or args.get("value") or args.get("contextId") or ""
        target_url = args.get("url")

        clients.append(
            FeignClient(
                name=class_name_of(type_decl, source),
                package=package,
                target_name=target_name,
                target_url=target_url,
                location=SourceLocation(file_path=file_path),
                methods=list(_extract_methods(type_decl, source)),
            )
        )

    return clients


def _extract_methods(type_decl: Node, source: bytes) -> list[FeignClientMethod]:
    methods: list[FeignClientMethod] = []

    for method in iter_methods(body_of(type_decl)):
        annotations = annotations_of(modifiers_of(method), source)
        for annotation_name, default_http_method in _HTTP_METHOD_ANNOTATIONS.items():
            if annotation_name not in annotations:
                continue
            args = annotations[annotation_name]
            path = args.get("value") or args.get("path") or ""
            methods.append(
                FeignClientMethod(
                    http_method=args.get("method", default_http_method),
                    path=path,
                    method_name=method_name_of(method, source),
                )
            )

    return methods
