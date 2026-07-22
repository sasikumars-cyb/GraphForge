"""Discovers `@RestController`/`@Controller` classes and their
`@GetMapping`/`@PostMapping`/etc. endpoints.
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
from app.indexer.models.architecture import Controller, Endpoint, SourceLocation

_CONTROLLER_ANNOTATIONS = ("RestController", "Controller")

_HTTP_METHOD_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}


def _join_paths(base_path: str, method_path: str) -> str:
    base = base_path.rstrip("/")
    method = (
        method_path if method_path.startswith("/") else f"/{method_path}" if method_path else ""
    )
    combined = f"{base}{method}"
    return combined or "/"


def extract_controllers(root: Node, source: bytes, file_path: str) -> list[Controller]:
    controllers: list[Controller] = []
    package = package_name(root, source)

    for type_decl in iter_type_declarations(root):
        annotations = annotations_of(modifiers_of(type_decl), source)
        if not any(name in annotations for name in _CONTROLLER_ANNOTATIONS):
            continue

        base_path = ""
        if "RequestMapping" in annotations:
            args = annotations["RequestMapping"]
            base_path = args.get("value") or args.get("path") or ""

        class_name = class_name_of(type_decl, source)
        location = SourceLocation(file_path=file_path)
        endpoints = list(_extract_endpoints(type_decl, source, base_path, location))

        controllers.append(
            Controller(
                name=class_name,
                package=package,
                base_path=base_path or "/",
                location=location,
                endpoints=endpoints,
            )
        )

    return controllers


def _extract_endpoints(
    type_decl: Node, source: bytes, base_path: str, location: SourceLocation
) -> list[Endpoint]:
    endpoints: list[Endpoint] = []

    for method in iter_methods(body_of(type_decl)):
        annotations = annotations_of(modifiers_of(method), source)

        for annotation_name, http_method in _HTTP_METHOD_ANNOTATIONS.items():
            if annotation_name not in annotations:
                continue
            args = annotations[annotation_name]
            method_path = args.get("value") or args.get("path") or ""
            endpoints.append(
                Endpoint(
                    http_method=http_method,
                    path=_join_paths(base_path, method_path),
                    handler_method=method_name_of(method, source),
                    location=location,
                )
            )

        if "RequestMapping" in annotations:
            args = annotations["RequestMapping"]
            method_path = args.get("value") or args.get("path") or ""
            endpoints.append(
                Endpoint(
                    http_method=args.get("method", "ANY"),
                    path=_join_paths(base_path, method_path),
                    handler_method=method_name_of(method, source),
                    location=location,
                )
            )

    return endpoints
