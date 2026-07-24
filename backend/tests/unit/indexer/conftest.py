"""Real tree-sitter parsers, shared across extractor unit tests - the same
grammars `SpringBootJavaParser`/`PythonParser` use, so these tests exercise
the exact parse tree the extractors run against in production, not a
stand-in."""

from collections.abc import Callable

import pytest
import tree_sitter_java as tsjava
import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

_JAVA_LANGUAGE = Language(tsjava.language())
_PYTHON_LANGUAGE = Language(tspython.language())


@pytest.fixture
def parse_java() -> Callable[[str], tuple[Node, bytes]]:
    parser = Parser(_JAVA_LANGUAGE)

    def _parse(source_text: str) -> tuple[Node, bytes]:
        source = source_text.encode("utf-8")
        return parser.parse(source).root_node, source

    return _parse


@pytest.fixture
def parse_python() -> Callable[[str], tuple[Node, bytes]]:
    parser = Parser(_PYTHON_LANGUAGE)

    def _parse(source_text: str) -> tuple[Node, bytes]:
        source = source_text.encode("utf-8")
        return parser.parse(source).root_node, source

    return _parse
