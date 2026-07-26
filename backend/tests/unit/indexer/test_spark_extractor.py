"""Unit tests for `extract_spark_table_reads`/`extract_spark_table_writes`
against small in-memory Python sources."""

from collections.abc import Callable

from tree_sitter import Node

from app.indexer.extractors.python.spark import (
    extract_spark_table_reads,
    extract_spark_table_writes,
)

ParsePython = Callable[[str], tuple[Node, bytes]]


def test_spark_read_table(parse_python: ParsePython) -> None:
    root, source = parse_python('spark.read.table("bronze.customers")\n')
    reads = extract_spark_table_reads(root, source, "job.py")
    assert len(reads) == 1
    assert reads[0].table_name == "bronze.customers"


def test_spark_table_shorthand(parse_python: ParsePython) -> None:
    root, source = parse_python('spark.table("silver.orders")\n')
    reads = extract_spark_table_reads(root, source, "job.py")
    assert reads[0].table_name == "silver.orders"


def test_spark_read_table_through_format_builder(parse_python: ParsePython) -> None:
    root, source = parse_python('spark.read.format("delta").table("raw.events")\n')
    reads = extract_spark_table_reads(root, source, "job.py")
    assert reads[0].table_name == "raw.events"


def test_path_based_load_is_not_a_table_read(parse_python: ParsePython) -> None:
    root, source = parse_python('spark.read.format("delta").load("/mnt/data/x")\n')
    assert extract_spark_table_reads(root, source, "job.py") == []


def test_table_call_not_rooted_at_spark_is_skipped(parse_python: ParsePython) -> None:
    root, source = parse_python('registry.table("not-a-spark-call")\n')
    assert extract_spark_table_reads(root, source, "job.py") == []


def test_read_inside_method_records_function_name(parse_python: ParsePython) -> None:
    source_text = (
        "class DeltaTableManager:\n"
        "    def merge_schema(self):\n"
        '        return spark.read.table("bronze.customers")\n'
    )
    root, source = parse_python(source_text)
    reads = extract_spark_table_reads(root, source, "job.py")
    assert reads[0].function_name == "merge_schema"


def test_write_save_as_table(parse_python: ParsePython) -> None:
    root, source = parse_python('df.write.mode("overwrite").saveAsTable("gold.report")\n')
    writes = extract_spark_table_writes(root, source, "job.py")
    assert len(writes) == 1
    assert writes[0].table_name == "gold.report"
    assert writes[0].method_name == "saveAsTable"


def test_write_insert_into(parse_python: ParsePython) -> None:
    root, source = parse_python('df.write.insertInto("gold.report")\n')
    writes = extract_spark_table_writes(root, source, "job.py")
    assert writes[0].method_name == "insertInto"


def test_non_literal_write_target_is_not_recorded(parse_python: ParsePython) -> None:
    root, source = parse_python('df.write.insertInto(f"gold.{name}")\n')
    assert extract_spark_table_writes(root, source, "job.py") == []


def test_write_inside_function_records_function_name(parse_python: ParsePython) -> None:
    source_text = (
        "def write_report(df):\n"
        '    df.write.saveAsTable("gold.report")\n'
        "\n"
        "def other():\n"
        '    df.write.saveAsTable("gold.other")\n'
    )
    root, source = parse_python(source_text)
    writes = extract_spark_table_writes(root, source, "job.py")
    by_table = {w.table_name: w.function_name for w in writes}
    assert by_table == {"gold.report": "write_report", "gold.other": "other"}
