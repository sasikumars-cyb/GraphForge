"""Unit tests for `extract_imports` against small in-memory Python sources -
faster and more targeted than the fixture-repo end-to-end parser test."""

from collections.abc import Callable

from tree_sitter import Node

from app.indexer.extractors.python.imports import extract_imports

ParsePython = Callable[[str], tuple[Node, bytes]]


def test_bare_import_has_no_imported_names(parse_python: ParsePython) -> None:
    root, source = parse_python("import os\n")
    imports = extract_imports(root, source, "mod.py")
    assert len(imports) == 1
    assert imports[0].module == "os"
    assert imports[0].imported_names == []


def test_dotted_bare_import(parse_python: ParsePython) -> None:
    root, source = parse_python("import os.path\n")
    imports = extract_imports(root, source, "mod.py")
    assert imports[0].module == "os.path"


def test_aliased_bare_import_records_the_original_module(parse_python: ParsePython) -> None:
    root, source = parse_python("import os.path as p\n")
    imports = extract_imports(root, source, "mod.py")
    assert imports[0].module == "os.path"


def test_from_import_with_multiple_and_aliased_names(parse_python: ParsePython) -> None:
    root, source = parse_python("from typing import List, Optional as Opt\n")
    imports = extract_imports(root, source, "mod.py")
    assert len(imports) == 1
    assert imports[0].module == "typing"
    assert imports[0].imported_names == ["List", "Optional"]


def test_relative_import_records_dots_in_module(parse_python: ParsePython) -> None:
    root, source = parse_python("from . import sibling\n")
    imports = extract_imports(root, source, "mod.py")
    assert imports[0].module == "."
    assert imports[0].imported_names == ["sibling"]


def test_wildcard_import_is_recorded_explicitly(parse_python: ParsePython) -> None:
    root, source = parse_python("from app.models import *\n")
    imports = extract_imports(root, source, "mod.py")
    assert imports[0].imported_names == ["*"]


def test_import_inside_a_function_is_still_found(parse_python: ParsePython) -> None:
    root, source = parse_python("def f():\n    import json\n    return json.dumps({})\n")
    imports = extract_imports(root, source, "mod.py")
    assert [i.module for i in imports] == ["json"]
