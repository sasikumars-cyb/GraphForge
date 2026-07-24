"""End-to-end (parser-level, no clone/graph/DB involved): walks the real
fixture Python project and checks every discovery category lands in the
merged `ArchitectureModel`.
"""

from pathlib import Path

from app.indexer.parsers.python.python_parser import PythonParser

FIXTURE_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "python_sample"


def test_parses_every_discovery_category() -> None:
    model = PythonParser().parse(FIXTURE_ROOT)

    assert model.language == "python"
    assert model.framework is None

    modules_by_name = {m.name: m for m in model.python_modules}
    assert "app.services.order_service" in modules_by_name
    assert "app.services.base_service" in modules_by_name
    assert "app.models.order" in modules_by_name

    order_service_module = modules_by_name["app.services.order_service"]
    assert order_service_module.package == "app.services"
    assert [f.module for f in order_service_module.imports] == [
        "app.models.order",
        "app.services.base_service",
    ]

    order_service = next(c for c in order_service_module.classes if c.name == "OrderService")
    assert order_service.bases == ["BaseService"]
    method_names = {m.name for m in order_service.methods}
    assert method_names == {"create_order", "save"}

    create_order = next(m for m in order_service.methods if m.name == "create_order")
    assert "self.save" in create_order.calls

    assert [f.name for f in order_service_module.functions] == ["build_default_service"]

    order_module = modules_by_name["app.models.order"]
    order_class = order_module.classes[0]
    assert order_class.name == "Order"
    assert order_class.decorators == ["dataclass"]

    dependency_names = {d.name for d in model.python_dependencies}
    assert dependency_names == {"fastapi", "pydantic"}


def test_init_module_name_drops_the_init_segment() -> None:
    model = PythonParser().parse(FIXTURE_ROOT)
    modules_by_name = {m.name: m for m in model.python_modules}
    assert "app" in modules_by_name
    assert "app.__init__" not in modules_by_name
