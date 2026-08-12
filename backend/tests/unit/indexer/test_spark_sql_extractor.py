"""Unit tests for `extract_spark_sql_references` — the `spark.sql(...)`
lineage path, including dynamic-SQL resolution and its explicit "skip
rather than guess" boundary."""

from collections.abc import Callable

from tree_sitter import Node

from app.indexer.extractors.python.spark import extract_spark_sql_references

ParsePython = Callable[[str], tuple[Node, bytes]]


def test_plain_literal_sql_records_both_read_and_write(parse_python: ParsePython) -> None:
    root, source = parse_python(
        'spark.sql("INSERT INTO catalog.schema.t SELECT * FROM catalog.schema.src")\n'
    )
    reads, writes = extract_spark_sql_references(root, source, "job.py")
    assert [r.table_name for r in reads] == ["catalog.schema.src"]
    assert [(w.table_name, w.method_name) for w in writes] == [("catalog.schema.t", "INSERT_INTO")]


def test_create_table_with_backticks_and_using_delta(parse_python: ParsePython) -> None:
    root, source = parse_python(
        'spark.sql("CREATE TABLE IF NOT EXISTS `cat`.`schema`.`t` (x INT) USING DELTA")\n'
    )
    reads, writes = extract_spark_sql_references(root, source, "job.py")
    assert reads == []
    assert [(w.table_name, w.method_name) for w in writes] == [("cat.schema.t", "CREATE_TABLE")]


def test_resolvable_fstring_local_constant_is_extracted(parse_python: ParsePython) -> None:
    source_text = (
        "def run():\n"
        '    table = "catalog.schema.customer"\n'
        '    spark.sql(f"SELECT * FROM {table}")\n'
    )
    root, source = parse_python(source_text)
    reads, writes = extract_spark_sql_references(root, source, "job.py")
    assert [r.table_name for r in reads] == ["catalog.schema.customer"]
    assert writes == []


def test_unresolvable_fstring_built_from_parameters_is_skipped(parse_python: ParsePython) -> None:
    # The exact shape from the ground-truth audit's table_manager.py /
    # bronze_writer.py: catalog/schema/table_name are function parameters,
    # not literals - table_id can never resolve to a literal string.
    source_text = (
        "def create(catalog, schema, table_name):\n"
        '    table_id = f"{catalog}.{schema}.{table_name}"\n'
        '    spark.sql(f"CREATE TABLE {table_id} (x INT)")\n'
    )
    root, source = parse_python(source_text)
    reads, writes = extract_spark_sql_references(root, source, "job.py")
    assert reads == []
    assert writes == []


def test_unresolvable_fstring_from_external_query_template_is_skipped(
    parse_python: ParsePython,
) -> None:
    # base_table_loader.py's actual shape: the query text itself is a
    # function parameter, not resolvable at all.
    source_text = (
        "def populate(query_template):\n"
        "    insert_query = query_template.format(catalog=1)\n"
        "    spark.sql(insert_query)\n"
    )
    root, source = parse_python(source_text)
    reads, writes = extract_spark_sql_references(root, source, "job.py")
    assert reads == []
    assert writes == []


def test_reassigned_local_is_treated_as_unresolvable(parse_python: ParsePython) -> None:
    # Two assignments to the same name - which one is "the" value at the
    # spark.sql() call site can't be known without tracking control flow,
    # so this must not guess at either.
    source_text = (
        "def run(flag):\n"
        '    table = "catalog.schema.a"\n'
        "    if flag:\n"
        '        table = "catalog.schema.b"\n'
        '    spark.sql(f"SELECT * FROM {table}")\n'
    )
    root, source = parse_python(source_text)
    reads, writes = extract_spark_sql_references(root, source, "job.py")
    assert reads == []
    assert writes == []


def test_call_not_rooted_at_spark_is_skipped(parse_python: ParsePython) -> None:
    root, source = parse_python('registry.sql("SELECT * FROM catalog.schema.t")\n')
    reads, writes = extract_spark_sql_references(root, source, "job.py")
    assert reads == []
    assert writes == []


def test_function_name_is_recorded_for_sql_calls(parse_python: ParsePython) -> None:
    source_text = (
        "def create_base_tables(context):\n"
        '    spark.sql("CREATE TABLE catalog.schema.t (x INT)")\n'
    )
    root, source = parse_python(source_text)
    _, writes = extract_spark_sql_references(root, source, "job.py")
    assert writes[0].function_name == "create_base_tables"


def test_module_level_call_has_no_function_name(parse_python: ParsePython) -> None:
    root, source = parse_python('spark.sql("CREATE TABLE catalog.schema.t (x INT)")\n')
    _, writes = extract_spark_sql_references(root, source, "job.py")
    assert writes[0].function_name is None


def test_merge_into_produces_write_and_read(parse_python: ParsePython) -> None:
    root, source = parse_python(
        'spark.sql("MERGE INTO catalog.schema.target USING catalog.schema.source ON a=b")\n'
    )
    reads, writes = extract_spark_sql_references(root, source, "job.py")
    assert [r.table_name for r in reads] == ["catalog.schema.source"]
    assert [(w.table_name, w.method_name) for w in writes] == [
        ("catalog.schema.target", "MERGE_INTO")
    ]


def test_implicit_adjacent_string_concatenation_is_resolved(parse_python: ParsePython) -> None:
    # A long spark.sql() argument split across adjacent string literals for
    # readability - concatenation of plain literals is exact, not a guess.
    source_text = (
        "def run_ingest():\n"
        "    spark.sql(\n"
        '        "INSERT INTO catalog.schema.t "\n'
        '        "SELECT * FROM catalog.schema.s"\n'
        "    )\n"
    )
    root, source = parse_python(source_text)
    reads, writes = extract_spark_sql_references(root, source, "job.py")
    assert [r.table_name for r in reads] == ["catalog.schema.s"]
    assert [(w.table_name, w.method_name) for w in writes] == [("catalog.schema.t", "INSERT_INTO")]


def test_concatenated_string_with_one_unresolvable_piece_is_skipped(
    parse_python: ParsePython,
) -> None:
    source_text = (
        "def run(table_name):\n"
        '    spark.sql(\n'
        '        "SELECT * FROM "\n'
        "        f\"{table_name}\"\n"
        "    )\n"
    )
    root, source = parse_python(source_text)
    reads, writes = extract_spark_sql_references(root, source, "job.py")
    assert reads == []
    assert writes == []
