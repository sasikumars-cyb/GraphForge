from collections.abc import Callable

from tree_sitter import Node

from app.indexer.extractors.kafka import extract_kafka_consumers, extract_kafka_producers

CONSUMER_SOURCE = """
package com.example.orders;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class OrderEventListener {

    @KafkaListener(topics = {"order-created", "order-updated"}, groupId = "orders-group")
    public void onOrderEvent(String payload) {
    }
}
"""

PRODUCER_SOURCE = """
package com.example.orders;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class OrderEventProducer {

    @Autowired
    private KafkaTemplate<String, String> kafkaTemplate;

    public void publishOrderCreated(String payload) {
        kafkaTemplate.send("order-created", payload);
    }

    public void publishOrderCancelled(String payload) {
        this.kafkaTemplate.send("order-cancelled", payload);
    }

    public void publishDynamic(String topic, String payload) {
        kafkaTemplate.send(topic, payload);
    }
}
"""


def test_extracts_consumer_with_array_topics_and_group_id(
    parse_java: Callable[[str], tuple[Node, bytes]],
) -> None:
    root, source = parse_java(CONSUMER_SOURCE)

    consumers = extract_kafka_consumers(root, source, "OrderEventListener.java")

    topics = {c.topic for c in consumers}
    assert topics == {"order-created", "order-updated"}
    assert all(c.class_name == "OrderEventListener" for c in consumers)
    assert all(c.method_name == "onOrderEvent" for c in consumers)
    assert all(c.group_id == "orders-group" for c in consumers)


def test_extracts_producer_calls_including_this_qualified(
    parse_java: Callable[[str], tuple[Node, bytes]],
) -> None:
    root, source = parse_java(PRODUCER_SOURCE)

    producers = extract_kafka_producers(root, source, "OrderEventProducer.java")

    by_topic = {p.topic: p for p in producers}
    assert set(by_topic) == {"order-created", "order-cancelled"}
    assert by_topic["order-created"].method_name == "publishOrderCreated"
    assert by_topic["order-cancelled"].method_name == "publishOrderCancelled"
    assert all(p.class_name == "OrderEventProducer" for p in producers)


def test_non_literal_topic_is_not_recorded(parse_java: Callable[[str], tuple[Node, bytes]]) -> None:
    root, source = parse_java(PRODUCER_SOURCE)

    producers = extract_kafka_producers(root, source, "OrderEventProducer.java")

    assert all(p.method_name != "publishDynamic" for p in producers)


def test_class_without_kafka_template_field_has_no_producers(
    parse_java: Callable[[str], tuple[Node, bytes]],
) -> None:
    root, source = parse_java(
        "package com.example.orders;\n\npublic class Plain {\n    public void noop() {}\n}\n"
    )

    assert extract_kafka_producers(root, source, "Plain.java") == []
