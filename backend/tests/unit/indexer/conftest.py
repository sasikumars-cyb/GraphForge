"""A real tree-sitter Java parser, shared across extractor unit tests -
same grammar `SpringBootJavaParser` uses, so these tests exercise the exact
parse tree the extractors run against in production, not a stand-in."""

from collections.abc import Callable

import pytest
import tree_sitter_java as tsjava
from tree_sitter import Language, Node, Parser

_JAVA_LANGUAGE = Language(tsjava.language())


@pytest.fixture
def parse_java() -> Callable[[str], tuple[Node, bytes]]:
    parser = Parser(_JAVA_LANGUAGE)

    def _parse(source_text: str) -> tuple[Node, bytes]:
        source = source_text.encode("utf-8")
        return parser.parse(source).root_node, source

    return _parse
