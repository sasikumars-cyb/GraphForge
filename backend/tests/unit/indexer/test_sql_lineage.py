"""Unit tests for `extract_sql_table_references` — pure SQL-text -> table
reference extraction, independent of where the SQL text came from."""

from app.indexer.extractors.sql_lineage import extract_sql_table_references


def _refs(sql: str) -> list[tuple[str, str, str]]:
    return [(r.access, r.statement, r.table_name) for r in extract_sql_table_references(sql)]


def test_create_table_if_not_exists_is_a_write() -> None:
    assert _refs("CREATE TABLE IF NOT EXISTS catalog.schema.t (x INT)") == [
        ("write", "CREATE_TABLE", "catalog.schema.t")
    ]


def test_create_or_replace_table_as_select_records_write_and_read() -> None:
    refs = _refs("CREATE OR REPLACE TABLE catalog.schema.out AS SELECT * FROM catalog.schema.inp")
    assert ("write", "CREATE_TABLE", "catalog.schema.out") in refs
    assert ("read", "SELECT", "catalog.schema.inp") in refs
    assert len(refs) == 2


def test_insert_into_select_records_write_and_read() -> None:
    refs = _refs("INSERT INTO catalog.schema.t SELECT * FROM catalog.schema.src")
    assert refs == [
        ("write", "INSERT_INTO", "catalog.schema.t"),
        ("read", "SELECT", "catalog.schema.src"),
    ]


def test_insert_overwrite_table_is_a_write() -> None:
    refs = _refs("INSERT OVERWRITE TABLE catalog.schema.t SELECT * FROM catalog.schema.src")
    assert ("write", "INSERT_OVERWRITE", "catalog.schema.t") in refs
    assert ("read", "SELECT", "catalog.schema.src") in refs


def test_insert_overwrite_without_table_keyword_is_a_write() -> None:
    assert _refs("INSERT OVERWRITE catalog.schema.t SELECT 1") == [
        ("write", "INSERT_OVERWRITE", "catalog.schema.t")
    ]


def test_insert_overwrite_directory_is_not_a_table_reference() -> None:
    # Writes to a filesystem path, not a table - must not be guessed at.
    assert _refs("INSERT OVERWRITE DIRECTORY '/mnt/data/x' SELECT * FROM catalog.schema.src") == [
        ("read", "SELECT", "catalog.schema.src")
    ]


def test_merge_into_using_distinguishes_target_write_from_source_read() -> None:
    refs = _refs(
        "MERGE INTO catalog.schema.target t USING catalog.schema.source s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.x = s.x"
    )
    assert ("write", "MERGE_INTO", "catalog.schema.target") in refs
    assert ("read", "MERGE_USING", "catalog.schema.source") in refs


def test_update_set_is_a_write_not_a_read() -> None:
    assert _refs("UPDATE catalog.schema.t SET x = 1 WHERE y = 2") == [
        ("write", "UPDATE", "catalog.schema.t")
    ]


def test_delete_from_is_a_write_not_a_read() -> None:
    # The explicit requirement: a DELETE's target must not also appear as
    # a generic FROM-read of itself.
    assert _refs("DELETE FROM catalog.schema.t WHERE x = 1") == [
        ("write", "DELETE", "catalog.schema.t")
    ]


def test_select_from_join_are_reads() -> None:
    refs = _refs(
        "SELECT * FROM catalog.schema.customer JOIN catalog.schema.orders ON a = b"
    )
    assert refs == [
        ("read", "SELECT", "catalog.schema.customer"),
        ("read", "JOIN", "catalog.schema.orders"),
    ]


def test_backtick_quoted_segments_are_normalized() -> None:
    refs = _refs("SELECT * FROM `catalog`.`schema`.`customer`")
    assert refs == [("read", "SELECT", "catalog.schema.customer")]


def test_backtick_and_bare_forms_of_the_same_table_normalize_identically() -> None:
    bare = extract_sql_table_references("SELECT * FROM catalog.schema.t")[0].table_name
    quoted = extract_sql_table_references("SELECT * FROM `catalog`.`schema`.`t`")[0].table_name
    assert bare == quoted


def test_using_delta_after_create_table_is_not_a_table_reference() -> None:
    # The false positive the original audit's ground-truth repo would have
    # tripped: `CREATE TABLE ... USING DELTA` - DELTA is a storage format,
    # not a table, and USING is only meaningful inside a MERGE INTO pair.
    assert _refs(
        "CREATE TABLE IF NOT EXISTS catalog.schema.t (x INT) USING DELTA PARTITIONED BY (x)"
    ) == [("write", "CREATE_TABLE", "catalog.schema.t")]


def test_comma_separated_from_list_is_read() -> None:
    refs = _refs("SELECT * FROM catalog.schema.a, catalog.schema.b")
    assert refs == [
        ("read", "SELECT", "catalog.schema.a"),
        ("read", "SELECT", "catalog.schema.b"),
    ]


def test_line_comment_content_is_not_scanned() -> None:
    refs = extract_sql_table_references("-- FROM should_not_appear\nSELECT * FROM catalog.schema.t")
    assert [r.table_name for r in refs] == ["catalog.schema.t"]
    assert refs[0].line == 2


def test_block_comment_content_is_not_scanned() -> None:
    refs = _refs("/* FROM should_not_appear */\nSELECT * FROM catalog.schema.t")
    assert refs == [("read", "SELECT", "catalog.schema.t")]


def test_subquery_is_not_extracted_as_a_table_but_inner_query_still_is() -> None:
    refs = _refs("SELECT * FROM (SELECT * FROM catalog.schema.inner) sub")
    assert refs == [("read", "SELECT", "catalog.schema.inner")]


def test_plain_select_with_no_from_produces_no_references() -> None:
    assert extract_sql_table_references("SELECT 1") == []


def test_empty_text_produces_no_references() -> None:
    assert extract_sql_table_references("") == []


def test_is_distinct_from_is_not_read_as_a_table_reference() -> None:
    # A real false positive found auditing a production repository's MERGE
    # statement: `IS DISTINCT FROM` is a comparison operator, not a FROM
    # clause - "source.start_datetime" must not become a "table".
    refs = _refs(
        "MERGE INTO catalog.schema.t USING catalog.schema.s ON a=b "
        "WHEN MATCHED AND (t.x IS DISTINCT FROM s.x) THEN UPDATE SET t.x = s.x"
    )
    assert ("read", "SELECT", "s.x") not in refs
    assert not any(r[2] == "s.x" for r in refs)


def test_extract_function_from_clause_is_not_a_table_reference() -> None:
    refs = _refs("SELECT EXTRACT(DAY FROM some_date) FROM catalog.schema.t")
    assert refs == [("read", "SELECT", "catalog.schema.t")]


def test_merge_into_using_subquery_still_extracts_the_write_target() -> None:
    # A real production shape: MERGE INTO target USING (<subquery>) AS
    # source - the source isn't a bare table, but the target must still be
    # recorded as a write, and the subquery's own inner tables must still
    # surface via the generic FROM/JOIN scan.
    refs = _refs(
        "MERGE INTO catalog.schema.target AS t "
        "USING (SELECT * FROM catalog.schema.inner_a JOIN catalog.schema.inner_b ON x=y) AS source "
        "ON t.id = source.id "
        "WHEN MATCHED THEN UPDATE SET t.x = source.x"
    )
    assert ("write", "MERGE_INTO", "catalog.schema.target") in refs
    assert ("read", "SELECT", "catalog.schema.inner_a") in refs
    assert ("read", "JOIN", "catalog.schema.inner_b") in refs
    # No spurious "MERGE_USING" read for the subquery itself.
    assert not any(r[1] == "MERGE_USING" for r in refs)
