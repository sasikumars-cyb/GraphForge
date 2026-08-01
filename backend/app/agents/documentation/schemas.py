"""Result shapes for the Documentation Agent (goal=review_documentation).

`DocumentationReviewResult` is the `AgentOutput.result` payload — read
directly by `DocumentationPage.tsx` and by the (optional) create-PR
endpoint, which reads `proposed_updates`/`proposed_new_documents` back off
a persisted run rather than recomputing them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FindingType = Literal["outdated", "missing", "duplicate", "broken_link", "needs_update"]
FindingSeverity = Literal["low", "medium", "high"]


class MarkdownFileSummary(BaseModel):
    """One Markdown file this run looked at."""

    path: str
    category: Literal["readme", "docs", "adr", "other"]
    size_bytes: int


class DocumentationFinding(BaseModel):
    """One reviewed-documentation finding — outdated, missing, duplicate,
    a broken internal link, or a file that otherwise needs updating."""

    finding_type: FindingType
    severity: FindingSeverity
    file_path: str
    description: str
    # Set only for finding_type="broken_link": the literal link target
    # text that didn't resolve to a real file in the repository.
    broken_link_target: str | None = None
    # Set only for finding_type="duplicate": the other file this one
    # substantially overlaps with.
    duplicate_of: str | None = None


class ProposedDocumentUpdate(BaseModel):
    """A proposed change to an existing Markdown file. Never applied to the
    repository automatically — `file_path` must already exist among the
    files this run discovered."""

    file_path: str
    rationale: str
    proposed_markdown: str


class ProposedNewDocument(BaseModel):
    """A proposed brand-new Markdown file for documentation this run found
    missing entirely (e.g. no README, an undocumented major component)."""

    file_path: str
    title: str
    rationale: str
    proposed_markdown: str


class DocumentationReviewResult(BaseModel):
    """The full result of one Documentation Agent run."""

    repository_full_name: str
    summary: str
    files_reviewed: list[MarkdownFileSummary] = Field(default_factory=list)
    findings: list[DocumentationFinding] = Field(default_factory=list)
    proposed_updates: list[ProposedDocumentUpdate] = Field(default_factory=list)
    proposed_new_documents: list[ProposedNewDocument] = Field(default_factory=list)
    confidence_reasoning: str = ""
    prompt_version: str = "1.0"
