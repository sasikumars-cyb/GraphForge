"""Markdown discovery and broken-internal-link detection.

Pure filesystem functions over an already-cloned repository directory (see
`app.indexer.scanner.repository_cloner.clone_repository`, reused —
not duplicated — by `DocumentationReviewAgent`) — no network, no DB, so
these are directly unit-testable against a plain temp directory.

Scope is Markdown only, per the Documentation Agent's own spec: README.md,
docs/**/*.md, ADR/**/*.md, and *.md generally. Since the first three are
all subsets of "every .md file in the repository", this walks the whole
tree once (skipping common vendor/build/VCS directories no one authors
documentation into) and classifies each match by its path, rather than
running four separate globs that would just re-find the same files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Directories that are never worth walking for authored documentation —
# skipping them outright (not filtering after the fact) keeps this fast on
# large repositories with vendored dependencies.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "venv",
        ".venv",
        "dist",
        "build",
        "target",
        "__pycache__",
        ".tox",
        "site-packages",
    }
)

_MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class MarkdownFile:
    """One discovered Markdown file."""

    # Relative to the repository root, forward-slash separated, e.g.
    # "docs/architecture/overview.md".
    relative_path: str
    absolute_path: Path
    category: str  # "readme" | "docs" | "adr" | "other"
    size_bytes: int


@dataclass(frozen=True)
class BrokenLink:
    source_file: str  # relative_path of the file containing the link
    target: str  # the literal link text that didn't resolve


def _categorize(relative_path: str) -> str:
    lower = relative_path.lower()
    name = lower.rsplit("/", 1)[-1]
    if name == "readme.md":
        return "readme"
    parts = lower.split("/")
    if "adr" in parts[:-1] or "adrs" in parts[:-1]:
        return "adr"
    if "docs" in parts[:-1] or "doc" in parts[:-1]:
        return "docs"
    return "other"


def discover_markdown_files(repo_root: Path) -> list[MarkdownFile]:
    """Every `*.md` file in `repo_root`, skipping vendor/build/VCS
    directories, classified by where it lives."""
    files: list[MarkdownFile] = []
    for path in sorted(repo_root.rglob("*.md")):
        if any(part in _SKIP_DIRS for part in path.relative_to(repo_root).parts[:-1]):
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(repo_root).as_posix()
        files.append(
            MarkdownFile(
                relative_path=relative,
                absolute_path=path,
                category=_categorize(relative),
                size_bytes=path.stat().st_size,
            )
        )
    return files


def _is_external_or_anchor(target: str) -> bool:
    stripped = target.strip()
    if not stripped or stripped.startswith("#"):
        return True
    return bool(
        re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", stripped)
    )  # any URI scheme (http:, mailto:, ...)


def find_broken_links(repo_root: Path, files: list[MarkdownFile]) -> list[BrokenLink]:
    """Markdown links `[text](relative/path)` whose target doesn't resolve
    to a real file in the repository. External links (any URL scheme) and
    same-page anchors (`#section`) are out of scope — only *internal*
    links are checked, per the Documentation Agent's own spec."""
    broken: list[BrokenLink] = []
    for file in files:
        try:
            text = file.absolute_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _MARKDOWN_LINK_PATTERN.finditer(text):
            target = match.group(1)
            if _is_external_or_anchor(target):
                continue
            target_path_part = target.split("#", 1)[0]
            resolved = (file.absolute_path.parent / target_path_part).resolve()
            try:
                resolved.relative_to(repo_root.resolve())
            except ValueError:
                # Resolves outside the repo entirely (e.g. "../../../etc") —
                # not a documentation defect this feature reports on.
                continue
            if not resolved.exists():
                broken.append(BrokenLink(source_file=file.relative_path, target=target))
    return broken


def find_duplicate_documents(files: list[MarkdownFile]) -> list[tuple[MarkdownFile, MarkdownFile]]:
    """Pairs of files whose content is byte-identical after whitespace
    normalization — the cheap, deterministic half of "duplicate
    documentation"; near-duplicates with real prose drift are left to the
    LLM synthesis pass, which sees full file content and can judge
    substantive overlap that an exact-match check can't."""
    normalized: dict[str, MarkdownFile] = {}
    pairs: list[tuple[MarkdownFile, MarkdownFile]] = []
    for file in files:
        try:
            text = file.absolute_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        key = " ".join(text.split())
        if not key:
            continue
        existing = normalized.get(key)
        if existing is not None:
            pairs.append((existing, file))
        else:
            normalized[key] = file
    return pairs
