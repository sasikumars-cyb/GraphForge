from collections.abc import Callable

from tree_sitter import Node

from app.indexer.extractors.services import extract_services


def test_extracts_service_annotated_class(parse_java: Callable[[str], tuple[Node, bytes]]) -> None:
    root, source = parse_java("""
package com.example.orders;

import org.springframework.stereotype.Service;

@Service
public class OrderService {
    public String placeOrder() {
        return "ok";
    }
}
""")

    services = extract_services(root, source, "OrderService.java")

    assert len(services) == 1
    assert services[0].name == "OrderService"
    assert services[0].package == "com.example.orders"
    assert services[0].location.file_path == "OrderService.java"


def test_class_without_service_annotation_is_ignored(
    parse_java: Callable[[str], tuple[Node, bytes]],
) -> None:
    root, source = parse_java(
        "package com.example.orders;\n\npublic class OrderDto {\n    private String id;\n}\n"
    )

    assert extract_services(root, source, "OrderDto.java") == []
