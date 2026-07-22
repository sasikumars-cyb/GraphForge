from collections.abc import Callable

from tree_sitter import Node

from app.indexer.extractors.controllers import extract_controllers

SOURCE = """
package com.example.orders;

import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    @GetMapping
    public String listOrders() {
        return "[]";
    }

    @GetMapping("/{id}")
    public String getOrder() {
        return "{}";
    }

    @PostMapping("/create")
    public String createOrder() {
        return "created";
    }

    @DeleteMapping("/{id}")
    public void deleteOrder() {
    }
}
"""


def test_extracts_controller_with_base_path(
    parse_java: Callable[[str], tuple[Node, bytes]],
) -> None:
    root, source = parse_java(SOURCE)

    controllers = extract_controllers(root, source, "OrderController.java")

    assert len(controllers) == 1
    controller = controllers[0]
    assert controller.name == "OrderController"
    assert controller.package == "com.example.orders"
    assert controller.base_path == "/api/orders"
    assert controller.location.file_path == "OrderController.java"


def test_extracts_every_endpoint_with_joined_path(
    parse_java: Callable[[str], tuple[Node, bytes]],
) -> None:
    root, source = parse_java(SOURCE)

    controller = extract_controllers(root, source, "OrderController.java")[0]

    endpoints = {(e.http_method, e.path, e.handler_method) for e in controller.endpoints}
    assert endpoints == {
        ("GET", "/api/orders", "listOrders"),
        ("GET", "/api/orders/{id}", "getOrder"),
        ("POST", "/api/orders/create", "createOrder"),
        ("DELETE", "/api/orders/{id}", "deleteOrder"),
    }


def test_plain_class_is_not_a_controller(parse_java: Callable[[str], tuple[Node, bytes]]) -> None:
    root, source = parse_java(
        "package com.example.orders;\n\npublic class OrderDto {\n    private String id;\n}\n"
    )

    assert extract_controllers(root, source, "OrderDto.java") == []
