"""Unit tests for `extract_sql_files` — repo-wide `.sql` file discovery,
independent of any Python/Java parsing."""

from pathlib import Path

from app.indexer.extractors.sql_file_extractor import extract_sql_files


def test_discovers_sql_files_and_their_table_references(tmp_path: Path) -> None:
    sql_dir = tmp_path / "pipeline" / "sql"
    sql_dir.mkdir(parents=True)
    (sql_dir / "account.sql").write_text(
        "SELECT * FROM catalog.schema.account_raw", encoding="utf-8"
    )
    (sql_dir / "insert_account.sql").write_text(
        "INSERT INTO catalog.schema.account SELECT * FROM catalog.schema.account_raw",
        encoding="utf-8",
    )

    sql_files, references = extract_sql_files(tmp_path)

    assert {f.name for f in sql_files} == {
        "pipeline/sql/account.sql",
        "pipeline/sql/insert_account.sql",
    }
    account_refs = [r for r in references if r.sql_file == "pipeline/sql/account.sql"]
    assert [(r.access, r.table_name) for r in account_refs] == [
        ("read", "catalog.schema.account_raw")
    ]
    insert_refs = [r for r in references if r.sql_file == "pipeline/sql/insert_account.sql"]
    assert ("write", "catalog.schema.account") in [(r.access, r.table_name) for r in insert_refs]
    assert ("read", "catalog.schema.account_raw") in [
        (r.access, r.table_name) for r in insert_refs
    ]


def test_no_sql_files_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("hello", encoding="utf-8")
    sql_files, references = extract_sql_files(tmp_path)
    assert sql_files == []
    assert references == []


def test_skip_directories_are_respected(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "vendored.sql").write_text(
        "SELECT * FROM catalog.schema.t", encoding="utf-8"
    )
    (tmp_path / "real.sql").write_text("SELECT * FROM catalog.schema.t", encoding="utf-8")

    sql_files, _ = extract_sql_files(tmp_path)
    assert [f.name for f in sql_files] == ["real.sql"]


def test_sql_file_with_no_recognized_statement_still_registers_as_a_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "empty.sql").write_text("-- just a comment\n", encoding="utf-8")
    sql_files, references = extract_sql_files(tmp_path)
    assert [f.name for f in sql_files] == ["empty.sql"]
    assert references == []
