"""Documentation health checks and scoring — pure functions over an
already-cloned repository directory.

No network, no DB, no LLM: every finding and the entire score are
computed deterministically here, so the same repository always scores the
same and the number is reproducible without a model in the loop. The LLM
only ever writes the narrative prose around these facts (see agent.py).

Markdown discovery itself is NOT re-implemented here — it reuses
`app.agents.documentation.discovery` (`discover_markdown_files`,
`find_broken_links`, `find_duplicate_documents`), which is the shared
Markdown layer for both documentation agents. This module adds only the
checks that layer doesn't already cover.

Extensibility (the stated design requirement): adding a health check is
two edits — one `FindingCategory` literal in schemas.py, and one `CHECKS`
entry plus a detector below. Scoring, capping, the breakdown the UI
renders, and the score itself all derive from `CHECKS`; none of them
need to change. A future Confluence/ADR-validation/coverage check plugs
in the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.agents.documentation.discovery import (
    MarkdownFile,
    find_broken_links,
    find_duplicate_documents,
)
from app.agents.documentation_health.schemas import (
    DocumentationStats,
    FindingCategory,
    FindingSeverity,
    HealthFinding,
    HealthGrade,
    MarkdownFileSummary,
    ScoreComponent,
)


@dataclass(frozen=True)
class CheckSpec:
    """How one category of finding affects the score.

    `penalty` is per finding; `max_penalty` caps the category's total so a
    repository with 200 broken links isn't scored identically to one with
    50 — both are "badly broken links", and letting one category consume
    the entire budget would hide every other signal.
    """

    penalty: float
    max_penalty: float
    severity: FindingSeverity


# The scoring table. Weights are deliberately blunt: a missing README is
# the single worst documentation state a repository can be in, structural
# gaps outrank cosmetic ones, and no single category can sink the score
# alone (see `max_penalty`).
CHECKS: dict[FindingCategory, CheckSpec] = {
    "missing_readme": CheckSpec(penalty=20.0, max_penalty=20.0, severity="high"),
    "missing_architecture_doc": CheckSpec(penalty=10.0, max_penalty=10.0, severity="medium"),
    "empty_document": CheckSpec(penalty=5.0, max_penalty=15.0, severity="high"),
    "placeholder_document": CheckSpec(penalty=3.0, max_penalty=12.0, severity="medium"),
    "duplicate_document": CheckSpec(penalty=5.0, max_penalty=15.0, severity="medium"),
    "duplicate_section": CheckSpec(penalty=2.0, max_penalty=10.0, severity="low"),
    "broken_link": CheckSpec(penalty=3.0, max_penalty=20.0, severity="medium"),
    "missing_toc": CheckSpec(penalty=2.0, max_penalty=8.0, severity="low"),
    "undocumented_folder": CheckSpec(penalty=4.0, max_penalty=12.0, severity="medium"),
    "missing_title": CheckSpec(penalty=2.0, max_penalty=8.0, severity="low"),
    "missing_ownership": CheckSpec(penalty=5.0, max_penalty=5.0, severity="low"),
    "missing_last_updated": CheckSpec(penalty=3.0, max_penalty=3.0, severity="low"),
}

# A document with content below this is a stub, not documentation. Chosen
# to sit above a one-line title + badge row (which is genuinely empty of
# content) and below any real page.
_PLACEHOLDER_MAX_CHARS = 200
# Documents past this many headings are long enough that readers navigate
# by table of contents rather than scrolling.
_TOC_HEADING_THRESHOLD = 6
# A source directory with at least this many code files and no Markdown
# anywhere beneath it reads as a genuinely undocumented area.
_UNDOCUMENTED_FOLDER_MIN_CODE_FILES = 10

_CODE_EXTENSIONS = frozenset(
    {".py", ".java", ".ts", ".tsx", ".js", ".jsx", ".go", ".rb", ".rs", ".kt", ".scala", ".cs"}
)
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
        "migrations",
    }
)

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_TOC_PATTERN = re.compile(r"table of contents|^\s*\*\s*\[.+\]\(#", re.IGNORECASE | re.MULTILINE)
_OWNERSHIP_PATTERN = re.compile(
    r"\b(owner|owners|maintainer|maintainers|codeowners)\b", re.IGNORECASE
)
_LAST_UPDATED_PATTERN = re.compile(r"last[\s_-]*updated", re.IGNORECASE)
_ARCHITECTURE_PATTERN = re.compile(r"architect|design|adr|system[\s_-]*overview", re.IGNORECASE)


def _read(file: MarkdownFile) -> str:
    try:
        return file.absolute_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _finding(category: FindingCategory, file_path: str, message: str) -> HealthFinding:
    """Build a finding with the severity `CHECKS` declares for its
    category, so severity can never drift from the scoring table."""
    return HealthFinding(
        category=category,
        severity=CHECKS[category].severity,
        file_path=file_path,
        message=message,
    )


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_missing_readme(files: list[MarkdownFile]) -> list[HealthFinding]:
    """A root README specifically — a README buried in a subdirectory
    doesn't orient someone landing on the repository."""
    if any(f.relative_path.lower() == "readme.md" for f in files):
        return []
    return [
        _finding(
            "missing_readme",
            "(repository)",
            "No README.md at the repository root — the first file a new reader looks for.",
        )
    ]


def check_missing_architecture_doc(files: list[MarkdownFile]) -> list[HealthFinding]:
    """Any ADR, or a doc whose path suggests architecture/design, counts."""
    for file in files:
        if file.category == "adr" or _ARCHITECTURE_PATTERN.search(file.relative_path):
            return []
    return [
        _finding(
            "missing_architecture_doc",
            "(repository)",
            "No architecture or design documentation found (no ADRs, no architecture/design page).",
        )
    ]


def check_empty_and_placeholder_documents(files: list[MarkdownFile]) -> list[HealthFinding]:
    findings: list[HealthFinding] = []
    for file in files:
        content = _read(file).strip()
        if not content:
            findings.append(_finding("empty_document", file.relative_path, "Document is empty."))
        elif len(content) < _PLACEHOLDER_MAX_CHARS:
            findings.append(
                _finding(
                    "placeholder_document",
                    file.relative_path,
                    f"Only {len(content)} characters of content — reads as a placeholder, "
                    "not documentation.",
                )
            )
    return findings


def check_duplicate_sections(files: list[MarkdownFile]) -> list[HealthFinding]:
    """Identical section bodies repeated across different files — the
    copy-paste drift that makes two pages disagree after one is edited.
    Compares whole sections (heading + body), not headings alone, since
    repeated headings like "## Usage" are normal and expected."""
    seen: dict[str, str] = {}  # normalized section body -> first file that had it
    findings: list[HealthFinding] = []
    for file in files:
        content = _read(file)
        parts = re.split(r"^#{1,6}\s+.+$", content, flags=re.MULTILINE)
        for body in parts[1:]:  # parts[0] is any preamble before the first heading
            normalized = " ".join(body.split())
            # Short bodies collide by coincidence (a one-line "TODO", a
            # single link); only substantial prose counts as duplication.
            if len(normalized) < 120:
                continue
            origin = seen.get(normalized)
            if origin is None:
                seen[normalized] = file.relative_path
            elif origin != file.relative_path:
                findings.append(
                    _finding(
                        "duplicate_section",
                        file.relative_path,
                        f"Contains a section identical to one in '{origin}'.",
                    )
                )
    return findings


def check_missing_toc(files: list[MarkdownFile]) -> list[HealthFinding]:
    findings: list[HealthFinding] = []
    for file in files:
        content = _read(file)
        headings = _HEADING_PATTERN.findall(content)
        if len(headings) >= _TOC_HEADING_THRESHOLD and not _TOC_PATTERN.search(content):
            findings.append(
                _finding(
                    "missing_toc",
                    file.relative_path,
                    f"{len(headings)} headings but no table of contents — hard to navigate.",
                )
            )
    return findings


def check_missing_title(files: list[MarkdownFile]) -> list[HealthFinding]:
    findings: list[HealthFinding] = []
    for file in files:
        content = _read(file).strip()
        if content and not _H1_PATTERN.search(content):
            findings.append(
                _finding(
                    "missing_title",
                    file.relative_path,
                    "No top-level '# Title' heading — inconsistent with a documented set.",
                )
            )
    return findings


def check_ownership_and_freshness(
    repo_root: Path, files: list[MarkdownFile]
) -> list[HealthFinding]:
    """Both are conditional on the project appearing to follow the
    convention at all, per the spec's "if the project follows such
    conventions": these are only reported when *some* documents carry the
    marker and others don't, or (for ownership) when a CODEOWNERS file
    exists but docs never name an owner. A project that has simply never
    used these conventions is not penalized for it."""
    findings: list[HealthFinding] = []
    non_empty = [f for f in files if _read(f).strip()]
    if not non_empty:
        return findings

    has_codeowners = any(
        (repo_root / candidate).exists()
        for candidate in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
    )
    with_owner = [f for f in non_empty if _OWNERSHIP_PATTERN.search(_read(f))]
    if (has_codeowners or with_owner) and len(with_owner) < len(non_empty):
        findings.append(
            _finding(
                "missing_ownership",
                "(repository)",
                f"This project records documentation ownership, but only "
                f"{len(with_owner)} of {len(non_empty)} documents name an owner.",
            )
        )

    with_updated = [f for f in non_empty if _LAST_UPDATED_PATTERN.search(_read(f))]
    if with_updated and len(with_updated) < len(non_empty):
        findings.append(
            _finding(
                "missing_last_updated",
                "(repository)",
                f"This project records a 'last updated' marker, but only "
                f"{len(with_updated)} of {len(non_empty)} documents carry one.",
            )
        )
    return findings


def check_undocumented_folders(repo_root: Path, files: list[MarkdownFile]) -> list[HealthFinding]:
    """Source directories carrying real code but no Markdown anywhere
    beneath them. Reported per top-level source directory rather than per
    leaf, so a large package produces one actionable finding instead of
    dozens."""
    documented_prefixes = {
        f.relative_path.rsplit("/", 1)[0] for f in files if "/" in f.relative_path
    }
    findings: list[HealthFinding] = []

    for entry in sorted(repo_root.iterdir()):
        if not entry.is_dir() or entry.name in _SKIP_DIRS or entry.name.startswith("."):
            continue
        code_files = 0
        has_markdown = False
        for path in entry.rglob("*"):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(repo_root).parts[:-1]
            if any(part in _SKIP_DIRS for part in relative_parts):
                continue
            if path.suffix == ".md":
                has_markdown = True
                break
            if path.suffix in _CODE_EXTENSIONS:
                code_files += 1
        if has_markdown or code_files < _UNDOCUMENTED_FOLDER_MIN_CODE_FILES:
            continue
        if any(prefix.startswith(entry.name) for prefix in documented_prefixes):
            continue
        findings.append(
            _finding(
                "undocumented_folder",
                entry.name,
                f"{code_files} code files with no Markdown documentation anywhere beneath it.",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_stats(files: list[MarkdownFile]) -> DocumentationStats:
    directories = {
        f.relative_path.rsplit("/", 1)[0] if "/" in f.relative_path else "(root)" for f in files
    }
    return DocumentationStats(
        total_markdown_files=len(files),
        readme_count=sum(1 for f in files if f.category == "readme"),
        docs_count=sum(1 for f in files if f.category == "docs"),
        adr_count=sum(1 for f in files if f.category == "adr"),
        other_count=sum(1 for f in files if f.category == "other"),
        total_documentation_bytes=sum(f.size_bytes for f in files),
        total_headings=sum(len(_HEADING_PATTERN.findall(_read(f))) for f in files),
        distinct_doc_directories=len(directories),
    )


def summarize_files(files: list[MarkdownFile]) -> list[MarkdownFileSummary]:
    return [
        MarkdownFileSummary(
            path=f.relative_path,
            category=f.category,  # type: ignore[arg-type]
            size_bytes=f.size_bytes,
            heading_count=len(_HEADING_PATTERN.findall(_read(f))),
        )
        for f in files
    ]


def analyze_documentation(repo_root: Path, files: list[MarkdownFile]) -> list[HealthFinding]:
    """Every check, in one pass. Broken links and duplicate documents come
    from the shared discovery layer (`app.agents.documentation.discovery`)
    rather than being re-implemented here."""
    findings: list[HealthFinding] = []
    findings += check_missing_readme(files)
    findings += check_missing_architecture_doc(files)
    findings += check_empty_and_placeholder_documents(files)
    findings += check_duplicate_sections(files)
    findings += check_missing_toc(files)
    findings += check_missing_title(files)
    findings += check_ownership_and_freshness(repo_root, files)
    findings += check_undocumented_folders(repo_root, files)

    for link in find_broken_links(repo_root, files):
        findings.append(
            _finding(
                "broken_link",
                link.source_file,
                f"Link target '{link.target}' does not resolve to a file in this repository.",
            )
        )
    for original, duplicate in find_duplicate_documents(files):
        findings.append(
            _finding(
                "duplicate_document",
                duplicate.relative_path,
                f"Content is identical to '{original.relative_path}'.",
            )
        )
    return findings


def score_findings(findings: list[HealthFinding]) -> tuple[int, HealthGrade, list[ScoreComponent]]:
    """Score = 100 minus each category's capped penalty, floored at 0.

    Returns the score, its grade band, and the per-category breakdown that
    makes the number explainable — the breakdown's penalties sum exactly
    to `100 - score` unless the floor clamped it.
    """
    counts: dict[FindingCategory, int] = {}
    for finding in findings:
        counts[finding.category] = counts.get(finding.category, 0) + 1

    breakdown: list[ScoreComponent] = []
    total_penalty = 0.0
    for category, count in sorted(counts.items(), key=lambda kv: -CHECKS[kv[0]].penalty * kv[1]):
        spec = CHECKS[category]
        raw = spec.penalty * count
        capped_penalty = min(raw, spec.max_penalty)
        total_penalty += capped_penalty
        breakdown.append(
            ScoreComponent(
                category=category,
                finding_count=count,
                penalty=round(capped_penalty, 1),
                capped=raw > spec.max_penalty,
            )
        )

    score = max(0, min(100, round(100 - total_penalty)))
    return score, grade_for(score), breakdown


def grade_for(score: int) -> HealthGrade:
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "fair"
    if score >= 40:
        return "poor"
    return "critical"
