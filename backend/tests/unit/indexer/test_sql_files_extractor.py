"""Unit tests for `extract_sql_file_references` — the Python-side
detection of a module's static reference to a `.sql` file (both the direct
`open(...)` rule and the literal-registry rule)."""

from collections.abc import Callable

from tree_sitter import Node

from app.indexer.extractors.python.sql_files import extract_sql_file_references

ParsePython = Callable[[str], tuple[Node, bytes]]


def test_direct_open_with_literal_sql_path(parse_python: ParsePython) -> None:
    root, source = parse_python('open("pipeline/sql/account.sql")\n')
    refs = extract_sql_file_references(root, source, "loader.py")
    assert [r.sql_filename for r in refs] == ["pipeline/sql/account.sql"]


def test_direct_open_with_resolvable_fstring(parse_python: ParsePython) -> None:
    source_text = 'sql_dir = "pipeline/sql"\nopen(f"{sql_dir}/account.sql")\n'
    root, source = parse_python(source_text)
    refs = extract_sql_file_references(root, source, "loader.py")
    assert [r.sql_filename for r in refs] == ["pipeline/sql/account.sql"]


def test_open_with_non_sql_extension_is_ignored(parse_python: ParsePython) -> None:
    root, source = parse_python('open("README.md")\n')
    assert extract_sql_file_references(root, source, "loader.py") == []


def test_open_with_path_built_via_slash_operator_is_not_resolved(
    parse_python: ParsePython,
) -> None:
    # The real query_loader.py shape: `SQL_DIR / filename` is a
    # `binary_operator`, not a string - correctly unresolvable, not
    # guessed at.
    source_text = "def load_sql(filename):\n    file_path = SQL_DIR / filename\n    open(file_path)\n"
    root, source = parse_python(source_text)
    assert extract_sql_file_references(root, source, "loader.py") == []


def test_open_function_name_is_recorded(parse_python: ParsePython) -> None:
    source_text = 'def load():\n    open("pipeline/sql/account.sql")\n'
    root, source = parse_python(source_text)
    refs = extract_sql_file_references(root, source, "loader.py")
    assert refs[0].function_name == "load"


def test_module_level_dict_registry_of_sql_filenames(parse_python: ParsePython) -> None:
    source_text = (
        "SQL_FILE_MAP = {\n"
        '    "account": "account.sql",\n'
        '    "party": "party.sql",\n'
        "}\n"
    )
    root, source = parse_python(source_text)
    refs = extract_sql_file_references(root, source, "sql_registry.py")
    assert {r.sql_filename for r in refs} == {"account.sql", "party.sql"}
    assert all(r.function_name is None for r in refs)


def test_module_level_list_registry_of_sql_filenames(parse_python: ParsePython) -> None:
    root, source = parse_python('SQL_FILES = ["a.sql", "b.sql"]\n')
    refs = extract_sql_file_references(root, source, "sql_registry.py")
    assert {r.sql_filename for r in refs} == {"a.sql", "b.sql"}


def test_registry_is_not_named_by_a_specific_identifier(parse_python: ParsePython) -> None:
    # The rule must not be keyed to the name "SQL_FILE_MAP" specifically -
    # any module-level dict of literal `.sql` values qualifies.
    root, source = parse_python('SOME_OTHER_NAME = {"x": "x.sql"}\n')
    refs = extract_sql_file_references(root, source, "whatever.py")
    assert [r.sql_filename for r in refs] == ["x.sql"]


def test_dict_values_that_are_not_sql_files_are_ignored(parse_python: ParsePython) -> None:
    root, source = parse_python('CONFIG = {"env": "prod", "region": "us-east-1"}\n')
    assert extract_sql_file_references(root, source, "config.py") == []


def test_non_literal_dict_values_are_ignored(parse_python: ParsePython) -> None:
    root, source = parse_python('SQL_FILE_MAP = {"account": some_function()}\n')
    assert extract_sql_file_references(root, source, "sql_registry.py") == []
