"""Unit tests for `extract_module_classes`/`extract_module_functions`
against small in-memory Python sources."""

from collections.abc import Callable

from tree_sitter import Node

from app.indexer.extractors.python.classes import extract_module_classes
from app.indexer.extractors.python.functions import extract_module_functions

ParsePython = Callable[[str], tuple[Node, bytes]]


def test_module_level_function_with_decorator_and_calls(parse_python: ParsePython) -> None:
    root, source = parse_python(
        "@lru_cache\ndef helper(x):\n    return validate(x) + normalize(x)\n"
    )
    functions = extract_module_functions(root, source, "mod.py")
    assert len(functions) == 1
    assert functions[0].name == "helper"
    assert functions[0].decorators == ["lru_cache"]
    assert set(functions[0].calls) == {"validate", "normalize"}


def test_class_with_bases_decorators_and_methods(parse_python: ParsePython) -> None:
    root, source = parse_python(
        "@dataclass\n"
        "class Foo(Base, ns.Other, metaclass=Meta):\n"
        "    def bar(self, x):\n"
        "        return self.baz(x)\n\n"
        "    @staticmethod\n"
        "    def qux():\n"
        "        pass\n"
    )
    classes = extract_module_classes(root, source, "mod.py")
    assert len(classes) == 1
    foo = classes[0]
    assert foo.name == "Foo"
    assert foo.bases == ["Base", "ns.Other"]
    assert foo.decorators == ["dataclass"]

    methods_by_name = {m.name: m for m in foo.methods}
    assert set(methods_by_name) == {"bar", "qux"}
    assert methods_by_name["bar"].calls == ["self.baz"]
    assert methods_by_name["qux"].decorators == ["staticmethod"]


def test_nested_function_calls_are_not_attributed_to_the_outer_function(
    parse_python: ParsePython,
) -> None:
    root, source = parse_python(
        "def outer():\n    def inner():\n        return leaf_call()\n    return inner()\n"
    )
    functions = extract_module_functions(root, source, "mod.py")
    assert len(functions) == 1
    assert functions[0].name == "outer"
    # outer() calls inner(), but not leaf_call() - that belongs to inner().
    assert functions[0].calls == ["inner"]


def test_class_with_no_bases_has_empty_bases_list(parse_python: ParsePython) -> None:
    root, source = parse_python("class Plain:\n    pass\n")
    classes = extract_module_classes(root, source, "mod.py")
    assert classes[0].bases == []
