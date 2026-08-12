"""Repo-wide `.sql` file discovery and parsing.

Deliberately not part of either `ILanguageParser` implementation:
`.sql` files commonly sit alongside Python (or Java) source rather than
being "the" detected language of a repository, so this runs unconditionally
after language-specific parsing (see `indexer/services/indexing_service.py`)
rather than being owned by `PythonParser`/`SpringBootJavaParser`. Adding a
third language later does not need to touch this module, and this module
does not need to know which language, if any, was detected.

No repository- or filename-specific logic anywhere here - `*.sql` is the
only pattern matched, the same `SKIP_DIRECTORIES` every other repo-wide
walk in this indexer already uses is respected, and table extraction goes
through the same generic `sql_lineage.extract_sql_table_references` a
`spark.sql(...)` call's resolved text goes through.
"""

from __future__ import annotations

from pathlib import Path

from app.indexer.extractors.sql_lineage import extract_sql_table_references
from app.indexer.models.architecture import SourceLocation, SqlFile, SqlTableReference
from app.indexer.scanner.skip_directories import SKIP_DIRECTORIES


def _iter_sql_files(repo_root: Path) -> list[Path]:
    return [
        path
        for path in repo_root.rglob("*.sql")
        if not any(part in SKIP_DIRECTORIES for part in path.parts)
    ]


def extract_sql_files(repo_root: Path) -> tuple[list[SqlFile], list[SqlTableReference]]:
    """Every `.sql` file in the repository, plus its table-level read/write
    references. A file that can't be read as UTF-8 text (a genuinely
    binary file with a `.sql` extension, or a permissions error) is
    skipped rather than raising - matching `PythonParser._read_source`'s
    own tolerance for unreadable files elsewhere in this pipeline.
    """
    sql_files: list[SqlFile] = []
    table_references: list[SqlTableReference] = []

    for path in _iter_sql_files(repo_root):
        relative_path = str(path.relative_to(repo_root))
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        sql_files.append(SqlFile(name=relative_path, location=SourceLocation(file_path=relative_path)))

        for ref in extract_sql_table_references(text):
            table_references.append(
                SqlTableReference(
                    sql_file=relative_path,
                    table_name=ref.table_name,
                    access=ref.access,
                    statement=ref.statement,
                    line=ref.line,
                )
            )

    return sql_files, table_references
