"""Tests for app.indexer.classification — the shared is_test/component
role classifier every indexed node's properties are derived from.

Regression coverage anchored on the real bug this closes: a Planning run
named `TestSCDType2Merger`/`TestExactDeduplicator` (real pytest test
classes in `tests/unit/test_scd2.py`/`tests/unit/test_dedup.py`) as if
they were the production `SCDType2Merger`/`ExactDeduplicator` they test.
"""

from __future__ import annotations

from app.indexer.classification import (
    classify,
    classify_is_test,
    production_sibling_name,
    symbol_type_for,
)


class TestClassifyIsTest:
    def test_test_directory_path_is_test(self):
        is_test, confidence = classify_is_test("tests/unit/test_scd2.py", "TestSCDType2Merger")
        assert is_test is True
        assert confidence == 1.0

    def test_production_path_and_name_is_not_test(self):
        is_test, confidence = classify_is_test("src/etl_core/scd/scd_type2.py", "SCDType2Merger")
        assert is_test is False
        assert confidence > 0.9

    def test_java_maven_test_source_root_is_test(self):
        # Maven/Gradle convention: src/test/java/... — a different path
        # shape than pytest's tests/, must still be recognized.
        is_test, _ = classify_is_test(
            "src/test/java/com/example/OrderServiceTest.java", "OrderServiceTest"
        )
        assert is_test is True

    def test_test_shaped_path_without_test_shaped_name_is_still_test(self):
        # A bare helper function inside a test module — the path convention
        # alone is enough, high confidence since it's the stronger signal.
        is_test, confidence = classify_is_test("tests/unit/test_dedup.py", "make_fixture_rows")
        assert is_test is True
        assert confidence == 0.9

    def test_test_shaped_name_without_test_shaped_path_is_weaker_signal(self):
        # Regression: a production class incidentally named like a test
        # (e.g. "TestConnectionPool" — a connection pool *for* tests) is
        # still flagged (name is real evidence), but at lower confidence
        # than a path-confirmed test, since this is the ambiguous case.
        is_test, confidence = classify_is_test("src/etl_core/util/pool.py", "TestConnectionPool")
        assert is_test is True
        assert confidence == 0.55

    def test_neither_signal_is_confidently_production(self):
        is_test, confidence = classify_is_test(
            "src/etl_core/dedup/exact_dedup.py", "ExactDeduplicator"
        )
        assert is_test is False
        assert confidence >= 0.9

    def test_bare_test_prefix_function_name(self):
        is_test, _ = classify_is_test("app/services/order_service.py", "test_something")
        assert is_test is True

    def test_test_suffix_name_shape(self):
        is_test, _ = classify_is_test("com/example/OrderServiceTest.java", "OrderServiceTest")
        assert is_test is True

    def test_conftest_path_is_test(self):
        is_test, confidence = classify_is_test("tests/conftest.py", "make_client")
        assert is_test is True
        assert confidence == 0.9


class TestSymbolTypeFor:
    def test_bare_function(self):
        assert symbol_type_for(["Component", "Function"], None) == "function"

    def test_method_has_class_name(self):
        assert symbol_type_for(["Component", "Function"], "SCDType2Merger") == "method"

    def test_class(self):
        assert symbol_type_for(["Component", "Class"], None) == "class"

    def test_controller(self):
        assert symbol_type_for(["Component", "Controller"], None) == "controller"

    def test_service(self):
        assert symbol_type_for(["Component", "Service"], None) == "service"

    def test_feign_client(self):
        assert symbol_type_for(["Component", "FeignClient"], None) == "feign_client"

    def test_module(self):
        assert symbol_type_for(["Component", "Module"], None) == "module"

    def test_generic_component_fallback(self):
        assert symbol_type_for(["Component"], None) == "component"


class TestClassify:
    def test_full_classification_for_test_class(self):
        result = classify(
            file_path="tests/unit/test_scd2.py",
            name="TestSCDType2Merger",
            labels=["Component", "Class"],
        )
        assert result.is_test is True
        assert result.confidence == 1.0
        assert result.symbol_type == "class"

    def test_full_classification_for_production_class(self):
        result = classify(
            file_path="src/etl_core/scd/scd_type2.py",
            name="SCDType2Merger",
            labels=["Component", "Class"],
        )
        assert result.is_test is False
        assert result.symbol_type == "class"


class TestProductionSiblingName:
    def test_strips_test_prefix(self):
        assert production_sibling_name("TestSCDType2Merger") == "SCDType2Merger"

    def test_strips_test_prefix_dedup(self):
        assert production_sibling_name("TestExactDeduplicator") == "ExactDeduplicator"

    def test_strips_test_prefix_windowed(self):
        assert production_sibling_name("TestWindowedDeduplicator") == "WindowedDeduplicator"

    def test_strips_test_underscore_prefix(self):
        assert production_sibling_name("test_exact_dedup") == "exact_dedup"

    def test_strips_test_suffix(self):
        assert production_sibling_name("OrderServiceTest") == "OrderService"

    def test_strips_test_underscore_suffix(self):
        assert production_sibling_name("order_service_test") == "order_service"

    def test_non_test_name_returns_none(self):
        assert production_sibling_name("SCDType2Merger") is None

    def test_bare_dotted_name_uses_last_segment(self):
        assert production_sibling_name("module.tests.TestFoo") == "Foo"

    def test_short_exact_test_word_not_stripped_to_empty(self):
        # "Test" alone (len 4) must not strip to an empty string sibling.
        assert production_sibling_name("Test") is None
