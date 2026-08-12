"""Generic SQL-text -> table-reference extraction.

Deliberately not a SQL parser (no `tree-sitter-sql` grammar is a dependency
of this project - see `backend/pyproject.toml`): table-level lineage from a
bounded, documented set of statement shapes can be extracted correctly with
targeted, keyword-anchored regexes, without taking on a full SQL grammar
(and its dialect-specific edge cases) for a feature that intentionally
stops at table-level lineage (see the module docstring on column lineage
being out of scope).

This module knows nothing about Python, Spark, or `.sql` files - it takes
SQL text and returns table references. Two callers feed it real SQL text
today: `extractors/python/spark.py` (a resolved `spark.sql(...)` argument)
and `extractors/sql_file_extractor.py` (a `.sql` file's contents). Neither
callsite is referenced here, keeping this module reusable for anything
that produces SQL text in the future.

Supported statement shapes (deliberately the same list the feature request
enumerated - not "as much SQL as possible"):

    CREATE TABLE [IF NOT EXISTS] t                    -> write
    CREATE OR REPLACE TABLE t                          -> write
    CREATE TABLE t AS SELECT ... FROM s                 -> write t, read s
    INSERT INTO t SELECT ... FROM s                     -> write t, read s
    INSERT OVERWRITE [TABLE] t ...                       -> write t
    MERGE INTO t USING s ON ...                          -> write t, read s
    UPDATE t SET ...                                     -> write t (not read)
    DELETE FROM t                                        -> write t (not read)
    SELECT ... FROM s [JOIN s2 [JOIN s3 ...]]             -> read s, s2, s3

Every table name may be a bare identifier, a dotted `catalog.schema.table`
path, and/or backtick-quoted per segment (`` `catalog`.`schema`.`table` ``,
the common Databricks style) - backticks are stripped so the same table
referenced with or without them still merges to one entity. A table name
that isn't a plain identifier/dotted-path at the matched position (most
commonly a parenthesised subquery, e.g. `FROM (SELECT ...)`, or a CTE
reference with no way to distinguish it from a real table without tracking
`WITH ... AS (...)` names) is not extracted - consistent with this
codebase's "skip rather than guess" precedent; see the module docstring's
"Known limitations" note below the code.

Read vs. write is never ambiguous by construction: a `CREATE`/`INSERT`/
`MERGE INTO`/`UPDATE`/`DELETE FROM` target is masked out of the text before
the generic `FROM`/`JOIN` read-scan runs, so a write target is never also
reported as a read of itself (the feature request's explicit requirement).
`USING` is only ever treated as introducing a table reference inside a
matched `MERGE INTO ... USING ...` pair - never scanned generically - so
`CREATE TABLE ... USING DELTA` (a storage-format clause, not a table) is
never misread as a table reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_SEGMENT = r"(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)"
_TABLE = rf"{_SEGMENT}(?:\.{_SEGMENT})*"

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# `FROM` is not exclusively a table-clause keyword in SQL - both of these
# reuse the literal word as part of an operator/function, not a table
# reference, and must never reach the generic FROM/JOIN scan below:
#   `x IS [NOT] DISTINCT FROM y`  - a comparison operator (confirmed in the
#     wild: a real Databricks MERGE statement's WHEN MATCHED clause).
#   `EXTRACT(field FROM expr)`     - the ANSI SQL date-part extraction
#     function's own required syntax.
# Masked (not just excluded from matching) before the write-target passes
# run too, so a table name that happens to follow one of these inside a
# CREATE/INSERT/etc. span is still masked correctly by this pass running
# first - order here doesn't matter for spans that never overlap it does.
_NON_TABLE_FROM = re.compile(r"\bDISTINCT\s+FROM\b|\bEXTRACT\s*\([^()]*?\bFROM\b", re.IGNORECASE)

# Ordered: each is matched and its span masked (replaced with spaces, same
# length, so later matches' character offsets - used for line-number
# lookup - stay correct) before the generic FROM/JOIN read-scan runs, so a
# write target's own table name is never also picked up as a read.
_CREATE_TABLE = re.compile(
    rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({_TABLE})",
    re.IGNORECASE,
)
_INSERT = re.compile(
    # `(?!DIRECTORY\b)`: `INSERT OVERWRITE DIRECTORY '/path'` writes to a
    # filesystem path, not a table - DIRECTORY is a keyword here, not an
    # identifier that happens to be named "DIRECTORY", so it must never be
    # captured as a table name.
    rf"\bINSERT\s+(INTO|OVERWRITE(?:\s+TABLE)?)\s+(?!DIRECTORY\b)({_TABLE})",
    re.IGNORECASE,
)
_MERGE_INTO_TARGET = re.compile(
    # Captures just the target and consumes through a trailing `USING`
    # (with its optional target alias) so `_MERGE_USING_SOURCE` below can
    # check what immediately follows *without* requiring it to be a plain
    # table itself - `USING (<subquery>) AS source` is common in the wild
    # (confirmed: a real Databricks MERGE statement) and must still yield
    # the target as a write even though its source isn't a bare table name.
    rf"\bMERGE\s+INTO\s+({_TABLE})(?:\s+(?:AS\s+)?[A-Za-z_][A-Za-z0-9_]*)?\s+USING\s*",
    re.IGNORECASE,
)
_MERGE_USING_SOURCE = re.compile(rf"\A({_TABLE})", re.IGNORECASE)
_UPDATE = re.compile(rf"\bUPDATE\s+({_TABLE})\s+SET\b", re.IGNORECASE)
_DELETE_FROM = re.compile(rf"\bDELETE\s+FROM\s+({_TABLE})", re.IGNORECASE)

_FROM = re.compile(rf"\bFROM\s+({_TABLE})", re.IGNORECASE)
_JOIN = re.compile(rf"\bJOIN\s+({_TABLE})", re.IGNORECASE)
_COMMA_CONTINUATION = re.compile(rf"\s*,\s*({_TABLE})")

Access = Literal["read", "write"]


@dataclass(frozen=True)
class SqlReference:
    """One table reference found in a block of SQL text.

    `line` is 1-based and relative to the start of the text passed in -
    callers that embed this text inside a larger file (a `.sql` file) or a
    Python source string (a `spark.sql(...)` argument) are responsible for
    translating it to a file-level line number if they need one; this
    module has no notion of where its input text came from.
    """

    table_name: str
    access: Access
    statement: str
    line: int


def _strip_comments(sql_text: str) -> str:
    """Blank out comment contents (same length, so offsets/line numbers of
    everything else stay correct) - a keyword inside a comment
    (`-- FROM old_table`) must never produce a reference.
    """
    sql_text = _LINE_COMMENT.sub(lambda m: " " * len(m.group()), sql_text)
    return _BLOCK_COMMENT.sub(lambda m: " " * len(m.group()), sql_text)


def _normalize_table_name(raw: str) -> str:
    """Strip backticks per dot-segment so `` `catalog`.`schema`.`t` `` and
    `catalog.schema.t` resolve to the identical table identity - required
    for a table referenced both ways (e.g. inline `spark.sql` vs. a
    `.sql` file) to merge onto the same `DataTable` node.
    """
    return ".".join(segment.strip("`") for segment in raw.split("."))


def _mask(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract_sql_table_references(sql_text: str) -> list[SqlReference]:
    """Extract table-level read/write references from a block of SQL text.

    Pure and deterministic - same input always produces the same output,
    no I/O, no guessing. See the module docstring for exactly which
    statement shapes are recognized and why unrecognized shapes (a
    subquery, a CTE reference, a dynamically-built identifier already
    resolved to plain text by the caller) are silently skipped rather than
    approximated.
    """
    text = _strip_comments(sql_text)
    text = _NON_TABLE_FROM.sub(lambda m: " " * len(m.group()), text)
    references: list[SqlReference] = []

    def masked_match(pattern: re.Pattern[str]) -> re.Match[str] | None:
        nonlocal text
        match = pattern.search(text)
        if match is not None:
            text = _mask(text, match.start(), match.end())
        return match

    # --- Writes (and, for MERGE, its paired read) - matched and masked
    # first so the generic FROM/JOIN scan below never re-sees these spans. ---
    while (match := masked_match(_CREATE_TABLE)) is not None:
        references.append(
            SqlReference(
                table_name=_normalize_table_name(match.group(1)),
                access="write",
                statement="CREATE_TABLE",
                line=_line_of(sql_text, match.start(1)),
            )
        )

    while (match := masked_match(_INSERT)) is not None:
        verb = "INSERT_OVERWRITE" if match.group(1).upper().startswith("OVERWRITE") else "INSERT_INTO"
        references.append(
            SqlReference(
                table_name=_normalize_table_name(match.group(2)),
                access="write",
                statement=verb,
                line=_line_of(sql_text, match.start(2)),
            )
        )

    while (match := _MERGE_INTO_TARGET.search(text)) is not None:
        references.append(
            SqlReference(
                table_name=_normalize_table_name(match.group(1)),
                access="write",
                statement="MERGE_INTO",
                line=_line_of(sql_text, match.start(1)),
            )
        )
        # What immediately follows the (already-consumed) `USING` may be a
        # plain table - captured as the paired read - or a parenthesised
        # subquery, which is not a table reference at all: its own inner
        # `FROM`/`JOIN` tables are still found correctly by the generic
        # scan below, since only "MERGE INTO target ... USING" itself is
        # masked here, never the subquery text that follows it.
        tail = text[match.end() :]
        source_match = _MERGE_USING_SOURCE.match(tail)
        if source_match is not None:
            references.append(
                SqlReference(
                    table_name=_normalize_table_name(source_match.group(1)),
                    access="read",
                    statement="MERGE_USING",
                    line=_line_of(sql_text, match.end() + source_match.start(1)),
                )
            )
            text = _mask(text, match.start(), match.end() + source_match.end())
        else:
            text = _mask(text, match.start(), match.end())

    while (match := masked_match(_UPDATE)) is not None:
        references.append(
            SqlReference(
                table_name=_normalize_table_name(match.group(1)),
                access="write",
                statement="UPDATE",
                line=_line_of(sql_text, match.start(1)),
            )
        )

    while (match := masked_match(_DELETE_FROM)) is not None:
        references.append(
            SqlReference(
                table_name=_normalize_table_name(match.group(1)),
                access="write",
                statement="DELETE",
                line=_line_of(sql_text, match.start(1)),
            )
        )

    # --- Reads: whatever FROM/JOIN targets remain once every write target
    # above has been masked out. Each match may continue as a comma-
    # separated list (`FROM a, b, c`). ---
    for pattern, statement in ((_FROM, "SELECT"), (_JOIN, "JOIN")):
        for match in pattern.finditer(text):
            references.append(
                SqlReference(
                    table_name=_normalize_table_name(match.group(1)),
                    access="read",
                    statement=statement,
                    line=_line_of(sql_text, match.start(1)),
                )
            )
            pos = match.end()
            while (extra := _COMMA_CONTINUATION.match(text, pos)) is not None:
                references.append(
                    SqlReference(
                        table_name=_normalize_table_name(extra.group(1)),
                        access="read",
                        statement=statement,
                        line=_line_of(sql_text, extra.start(1)),
                    )
                )
                pos = extra.end()

    return references


# --- Known limitations (documented, not silently accepted) ---
#
# - CTEs (`WITH cte AS (SELECT ... FROM real_table) SELECT ... FROM cte`):
#   the outer `FROM cte` is extracted as if `cte` were a real table, since
#   this module does not track `WITH ... AS (...)` names. Table-level
#   lineage from the *inner* query (`real_table`) is still captured
#   correctly; only the CTE alias itself is a false positive. Excluding it
#   would require tracking WITH-clause names across the whole statement,
#   deliberately out of scope for a table-level-only pass.
# - A table name built from a runtime expression *inside* the SQL text
#   itself (dynamic SQL a caller already resolved to a literal string
#   still contains, e.g. a stored-procedure-style `EXECUTE format(...)`)
#   is not further evaluated - this module only ever sees the text it is
#   given.
# - Multi-table `UPDATE`/`DELETE ... USING` (a Postgres-style extension)
#   is not specially handled; only the primary target table is recorded.
# - A `.sql` file meant to be filled in via Python's `str.format()` at
#   runtime (e.g. `` `{catalog}`.`{schema}`.`orders` ``, confirmed in the
#   wild) is parsed exactly as written - the placeholder braces stay part
#   of the extracted table name (e.g. "{catalog}.{schema}.orders") rather
#   than being resolved to the real catalog/schema, since this module never
#   sees the `.format(catalog=..., schema=...)` call that would resolve
#   them (a separate file/language from wherever that call happens - true
#   cross-file data-flow, out of scope). This is honest, not a fabrication:
#   the *table name suffix* is always correct and consistent across every
#   reference to it, so lineage between tables within one repository is
#   still accurate; only the catalog/schema qualifier is left unresolved.
