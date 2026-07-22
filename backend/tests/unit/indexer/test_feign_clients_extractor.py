from collections.abc import Callable

from tree_sitter import Node

from app.indexer.extractors.feign_clients import extract_feign_clients

SOURCE = """
package com.example.orders;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;

@FeignClient(name = "payment-service", url = "http://payment-service:8080")
public interface PaymentClient {

    @PostMapping("/api/payments/charge")
    String charge();

    @GetMapping("/api/payments/{id}")
    String getPayment();
}
"""


def test_extracts_feign_client_target(parse_java: Callable[[str], tuple[Node, bytes]]) -> None:
    root, source = parse_java(SOURCE)

    clients = extract_feign_clients(root, source, "PaymentClient.java")

    assert len(clients) == 1
    client = clients[0]
    assert client.name == "PaymentClient"
    assert client.target_name == "payment-service"
    assert client.target_url == "http://payment-service:8080"


def test_extracts_remote_call_methods(parse_java: Callable[[str], tuple[Node, bytes]]) -> None:
    root, source = parse_java(SOURCE)

    client = extract_feign_clients(root, source, "PaymentClient.java")[0]

    methods = {(m.http_method, m.path, m.method_name) for m in client.methods}
    assert methods == {
        ("POST", "/api/payments/charge", "charge"),
        ("GET", "/api/payments/{id}", "getPayment"),
    }


def test_interface_without_feign_client_is_ignored(
    parse_java: Callable[[str], tuple[Node, bytes]],
) -> None:
    root, source = parse_java(
        "package com.example.orders;\n\npublic interface PlainInterface {\n    String noop();\n}\n"
    )

    assert extract_feign_clients(root, source, "PlainInterface.java") == []
