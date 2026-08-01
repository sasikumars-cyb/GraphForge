"""Deterministic relationship discovery between Markdown documents.

Reuses `app.agents.documentation.discovery.discover_markdown_files` for the
actual file walk — not duplicated here, per "do not duplicate repository
scanning logic" (see that module's own docstring). This module adds one
thing on top: which documents reference which, via real internal Markdown
links — the same link-extraction idea `find_broken_links` uses, but keeping
resolved (not broken) links instead of discarding them.

Pure filesystem functions over an already-cloned repository directory — no
network, no DB, no LLM call: relationships the agent reports as "discovered"
are always real edges between real files, never an LLM's guess at how
documents relate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.agents.documentation.discovery import MarkdownFile

_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_URI_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


@dataclass(frozen=True)
class DocumentLink:
    """One resolved internal link from one Markdown file to another."""

    from_file: str
    to_file: str


def discover_relationships(repo_root: Path, files: list[MarkdownFile]) -> list[DocumentLink]:
    """Every internal Markdown link that resolves to another discovered
    Markdown file, deduplicated per (from, to) pair. External links, page
    anchors, and links to non-Markdown files are out of scope — those
    aren't "relationships between documents"."""
    known_paths = {f.relative_path for f in files}
    resolved_root = repo_root.resolve()
    edges: list[DocumentLink] = []
    seen: set[tuple[str, str]] = set()

    for file in files:
        try:
            text = file.absolute_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _LINK_PATTERN.finditer(text):
            target = match.group(1).split("#", 1)[0].strip()
            if not target or _URI_SCHEME.match(target):
                continue
            resolved = (file.absolute_path.parent / target).resolve()
            try:
                relative = resolved.relative_to(resolved_root).as_posix()
            except ValueError:
                continue
            if relative == file.relative_path or relative not in known_paths:
                continue
            pair = (file.relative_path, relative)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(DocumentLink(from_file=file.relative_path, to_file=relative))

    return edges
