"""Shared AST-scanning helpers for the architecture-boundary tests in
this package.

Deliberately dependency-free (stdlib `ast` + `pathlib` only) and DB-free —
these tests run over the source tree on disk, not over an importable,
running application, so a boundary violation is caught even in a module
graph that would otherwise fail to import for unrelated reasons.
"""

from __future__ import annotations

import ast
from pathlib import Path

# app/ is this file's great-grandparent: backend/tests/unit/architecture/_source_scan.py
APP_ROOT = Path(__file__).resolve().parents[3] / "app"


def iter_python_files(*, exclude_dirs: frozenset[str] = frozenset({"__pycache__"})) -> list[Path]:
    """Every `.py` file under `app/`, excluding `__pycache__`."""
    return [p for p in APP_ROOT.rglob("*.py") if not any(part in exclude_dirs for part in p.parts)]


def _module_name_from_import(node: ast.Import | ast.ImportFrom) -> list[str]:
    """The dotted module path(s) an import statement references, as
    strings, without resolving relative imports (none of this repo's
    `app/` code uses them for cross-module imports at the depth these
    checks care about)."""
    if isinstance(node, ast.ImportFrom):
        return [node.module] if node.module else []
    return [alias.name for alias in node.names]


def find_imports_of(module_path: str, symbol: str | None = None) -> dict[Path, list[int]]:
    """Every file under `app/` that imports `module_path` (e.g.
    `"app.tools.executor"`), optionally narrowed to a specific `symbol`
    imported `from` it (e.g. `"ToolExecutor"`).

    Returns `{file: [line_numbers]}` for every match. A file appearing in
    the result imported the target at least once; callers compare the
    resulting key set against an explicit allowlist.
    """
    hits: dict[Path, list[int]] = {}
    for path in iter_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            # Not this test's concern — ruff/mypy/pytest collection already
            # catch unparseable source; skip rather than mask that failure
            # behind an architecture-test error.
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            names = _module_name_from_import(node)
            if module_path not in names:
                continue
            if symbol is not None:
                if not isinstance(node, ast.ImportFrom):
                    continue
                if symbol not in {alias.name for alias in node.names}:
                    continue
            hits.setdefault(path, []).append(node.lineno)
    return hits


def find_imports_matching_prefix(module_prefix: str) -> dict[Path, list[int]]:
    """Every file under `app/` that imports ANY module whose dotted path
    starts with `module_prefix` (e.g. `"app.tools.implementations"`
    matches `app.tools.implementations.github_tool`,
    `app.tools.implementations.jira_tool`, ...).

    Unlike `find_imports_of`, this is AST-based, not a textual substring
    search — it only matches real `import`/`from ... import` statements,
    never a docstring or comment that merely *mentions* the module path
    in prose (a real, confirmed false-positive class the first version of
    this check produced: `app/agents/git_ops/_authorization.py`'s own
    docstring literally names `app.tools.implementations.jira_tool` as
    prose, with no import anywhere in the file).
    """
    hits: dict[Path, list[int]] = {}
    for path in iter_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for name in _module_name_from_import(node):
                if name == module_prefix or name.startswith(module_prefix + "."):
                    hits.setdefault(path, []).append(node.lineno)
                    break
    return hits


def relative(path: Path) -> str:
    """`path` rendered relative to `app/`'s parent, for readable
    assertion failure messages (e.g. `app/services/foo.py`)."""
    return str(path.relative_to(APP_ROOT.parent))
