"""Pure CODEOWNERS parsing - no I/O, no knowledge of `AgentState` or tools.

Mirrors real CODEOWNERS semantics closely enough for the reviewer-suggestion
fallback's purpose: later matching lines override earlier ones for the same
path (last-match-wins), comments (`#`) and blank lines are skipped, and
patterns are matched via `fnmatch` glob semantics rather than a full
gitignore-style matcher.
"""

from __future__ import annotations

import fnmatch


def parse_codeowners(content: str) -> list[tuple[str, list[str]]]:
    """Ordered `(pattern, owners)` pairs, comments/blanks already stripped."""
    entries: list[tuple[str, list[str]]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        pattern, owners = parts[0], parts[1:]
        entries.append((pattern, owners))
    return entries


def match_owners(paths: set[str], content: str) -> dict[str, list[str]]:
    """For each path, the owners of the last matching pattern - CODEOWNERS'
    real last-match-wins rule, so a later, more specific override always
    beats an earlier, broader pattern. A path matching nothing gets `[]`."""
    entries = parse_codeowners(content)
    result: dict[str, list[str]] = {path: [] for path in paths}
    for path in paths:
        for pattern, owners in entries:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch("/" + path, pattern):
                result[path] = owners
    return result
