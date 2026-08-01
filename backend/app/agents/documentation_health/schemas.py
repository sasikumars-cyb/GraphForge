"""Result shapes for the Documentation Health Agent
(goal=analyze_documentation_health).

`DocumentationHealthReport` is the `AgentOutput.result` payload, read
directly by `DocumentationHealthPage.tsx`. Read-only by construction:
there is no "proposed change" field anywhere in this module — this MVP
never edits, commits, or opens anything (see the manifest's non-goals).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Every check this agent can emit. Adding a category here plus one entry in
# `analysis.CHECKS` is the whole cost of adding a new health check — see
# that module's docstring on extensibility.
FindingCategory = Literal[
    "missing_readme",
    "missing_architecture_doc",
    "empty_document",
    "placeholder_document",
    "duplicate_document",
    "duplicate_section",
    "broken_link",
    "missing_toc",
    "undocumented_folder",
    "missing_title",
    "missing_ownership",
    "missing_last_updated",
]

FindingSeverity = Literal["low", "medium", "high"]

HealthGrade = Literal["excellent", "good", "fair", "poor", "critical"]


class MarkdownFileSummary(BaseModel):
    """One Markdown file this run looked at."""

    path: str
    category: Literal["readme", "docs", "adr", "other"]
    size_bytes: int
    heading_count: int = 0


class HealthFinding(BaseModel):
    """One documentation-health observation."""

    category: FindingCategory
    severity: FindingSeverity
    # "(repository)" for repository-level findings that aren't about one
    # specific file (e.g. missing_readme, undocumented_folder).
    file_path: str
    message: str


class ScoreComponent(BaseModel):
    """One category's contribution to the final score — what makes the
    number explainable rather than a bare verdict. `penalty` is already
    capped (see `analysis.CHECKS[...].max_penalty`), so these sum exactly
    to `100 - score`."""

    category: FindingCategory
    finding_count: int
    penalty: float
    capped: bool = False


class DocumentationStats(BaseModel):
    """Plain counts — the "Overall Documentation Health" numbers, kept
    separate from findings so the UI can show scale alongside problems."""

    total_markdown_files: int = 0
    readme_count: int = 0
    docs_count: int = 0
    adr_count: int = 0
    other_count: int = 0
    total_documentation_bytes: int = 0
    total_headings: int = 0
    distinct_doc_directories: int = 0


class DocumentationHealthReport(BaseModel):
    """The full result of one Documentation Health run."""

    repository_full_name: str
    health_score: int = Field(ge=0, le=100)
    grade: HealthGrade
    summary: str = ""
    stats: DocumentationStats = Field(default_factory=DocumentationStats)
    files_reviewed: list[MarkdownFileSummary] = Field(default_factory=list)
    findings: list[HealthFinding] = Field(default_factory=list)
    score_breakdown: list[ScoreComponent] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    areas_for_improvement: list[str] = Field(default_factory=list)
    suggested_next_actions: list[str] = Field(default_factory=list)
    prompt_version: str = "1.0"
