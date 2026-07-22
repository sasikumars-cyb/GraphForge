"""End-to-end (parser-level, no clone/graph/DB involved): walks the real
fixture Spring Boot project and checks every discovery category lands in
the merged `ArchitectureModel`.
"""

from pathlib import Path

from app.indexer.parsers.java.spring_boot_parser import SpringBootJavaParser

FIXTURE_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "spring_boot_sample"


def test_parses_every_discovery_category() -> None:
    model = SpringBootJavaParser().parse(FIXTURE_ROOT)

    assert model.language == "java"
    assert model.framework == "spring-boot"

    assert [c.name for c in model.controllers] == ["OrderController"]
    assert len(model.controllers[0].endpoints) == 4

    assert [s.name for s in model.services] == ["OrderService"]

    assert [f.name for f in model.feign_clients] == ["PaymentClient"]
    assert model.feign_clients[0].target_name == "payment-service"

    consumer_topics = {c.topic for c in model.kafka_consumers}
    assert consumer_topics == {"order-created", "order-updated"}

    producer_topics = {p.topic for p in model.kafka_producers}
    assert producer_topics == {"order-created", "order-cancelled"}

    dependency_artifacts = {d.artifact_id for d in model.maven_dependencies}
    assert "spring-boot-starter-web" in dependency_artifacts
    assert "spring-cloud-dependencies" not in dependency_artifacts


def test_plain_dto_contributes_nothing() -> None:
    model = SpringBootJavaParser().parse(FIXTURE_ROOT)

    all_component_names = {c.name for c in model.controllers} | {s.name for s in model.services}
    assert "OrderDto" not in all_component_names
