"""Directory names skipped by every file-walking scan in the indexer -
factored out of `parsers/python/python_parser.py` (the original, sole
owner of this set) so `extractors/sql_file_extractor.py` can walk the repo
tree for `.sql` files the same way without duplicating - and risking
drifting from - the same list.
"""

SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        "build",
        "dist",
        "site-packages",
    }
)
